// Continuous quality loop for infinite-craft-cli:
// cull useless tests → drop seams they forced → push pure logic into
// craft.sudo → thin/dedupe Python+JS hosts → simplify → verify → repeat
// until residual is dry, residual stops shrinking, or max_rounds.
//
// Convergence model (the point of this script):
//   - Findings are finite defects with a greppable `signal` and stable `key`.
//   - A residual ledger carries unsolved keys across rounds.
//   - "Solved" requires signal-proof (signal gone / replacement present), not ok=true.
//   - Invent (new findings) is capped; after a no-shrink round, invent is off until residual shrinks.
//   - Flat invent forever is a bug; re-enumeration of residual is intentional.
//
// Args (all optional):
//   max_rounds: int (default 3, min 1) — hard brake on discover→verify cycles
//   max_actions_per_round: int (optional) — ONLY if set >0, hard-cap planned actions (debug)
//   max_new_findings_per_round: int (default 5, min 0) — invent budget; residual recheck is unlimited
//   dry_run: bool — discover + plan only, no edits
//   approve_each_round: bool — await_user before implement
//   focus: "all" | "tests" | "seams" | "kernel" | "hosts" | "simplify"
//   base_ref: git ref for optional pre-diff context (default "HEAD")
//   verify_cmd: override bazel verify command (string)
//   act_mode: "sequential" (default) | "batch"
//   use_grok_implementer: bool (default false)
//
// Agent budget (workflow tool agent_budget) is the cost ceiling for sequential acts.

let meta = #{
    name: "cleanup-loop",
    description: "Loop: residual-ledger cleanup — cull tests, drop seams, dual-host kernel extract, thin hosts; prove signals; stop when residual dries or stops shrinking",
    when_to_use: "Maintainability pass when hosts duplicate pure rules, tests assert implementation, or craft.sudo should own more business logic — not open-ended polish farming",
    phases: [
        #{ title: "Discover", detail: "residual recheck first; capped invent scanners if allowed" },
        #{ title: "Plan", detail: "actions from residual + admitted new only" },
        #{ title: "Act", detail: "implement planned actions" },
        #{ title: "Verify", detail: "bazel gate + signal proof; update residual ledger" },
        #{ title: "Loop", detail: "shrink residual or stop inventing / exit" },
    ],
};

// --- schemas -----------------------------------------------------------------

// Work orders only. Every finding needs a stable key + greppable signal.
// signal: substring that must be GONE after a successful fix (grep evidence).
// For remedy=replace, also describe what must appear in replacement (prose);
// signal still targets the bad artifact that must leave.
// key: stable id = kind|primary_path|signal_slug — same problem keeps same key across rounds.
let findings_schema = #{
    "type": "object",
    "required": ["findings", "dimension"],
    "properties": #{
        "dimension": #{ "type": "string" },
        "findings": #{
            "type": "array",
            "maxItems": 8,
            "items": #{
                "type": "object",
                "required": [
                    "id", "key", "kind", "remedy", "title", "detail",
                    "paths", "confidence", "replacement", "signal",
                ],
                "properties": #{
                    "id": #{ "type": "string" },
                    "key": #{ "type": "string" },
                    "kind": #{
                        "type": "string",
                        "enum": ["cull_test", "drop_seam", "kernel_extract", "host_dedup", "simplify"],
                    },
                    "remedy": #{
                        "type": "string",
                        "enum": ["delete", "replace", "extract", "inline", "refactor"],
                    },
                    "title": #{ "type": "string" },
                    "detail": #{ "type": "string" },
                    "paths": #{
                        "type": "array",
                        "items": #{ "type": "string" },
                        "maxItems": 8,
                    },
                    "confidence": #{ "type": "string", "enum": ["high", "medium", "low"] },
                    "replacement": #{ "type": "string" },
                    "signal": #{ "type": "string" },
                },
            },
        },
        "notes": #{ "type": "string" },
    },
};

// Residual recheck: is each open key's signal still present in its paths?
let residual_schema = #{
    "type": "object",
    "required": ["results"],
    "properties": #{
        "results": #{
            "type": "array",
            "maxItems": 80,
            "items": #{
                "type": "object",
                "required": ["key", "still_present", "evidence"],
                "properties": #{
                    "key": #{ "type": "string" },
                    "still_present": #{ "type": "boolean" },
                    "evidence": #{ "type": "string" },
                },
            },
        },
        "notes": #{ "type": "string" },
    },
};

// Signal proof after act: did the planned keys clear?
let signal_proof_schema = #{
    "type": "object",
    "required": ["checks"],
    "properties": #{
        "checks": #{
            "type": "array",
            "maxItems": 80,
            "items": #{
                "type": "object",
                "required": ["key", "cleared", "evidence"],
                "properties": #{
                    "key": #{ "type": "string" },
                    "cleared": #{ "type": "boolean" },
                    "evidence": #{ "type": "string" },
                },
            },
        },
        "notes": #{ "type": "string" },
    },
};

let plan_schema = #{
    "type": "object",
    "required": ["actions", "rationale", "done"],
    "properties": #{
        "done": #{ "type": "boolean" },
        "rationale": #{ "type": "string" },
        "actions": #{
            "type": "array",
            "maxItems": 40,
            "items": #{
                "type": "object",
                "required": ["id", "key", "kind", "remedy", "title", "spec", "paths", "signal"],
                "properties": #{
                    "id": #{ "type": "string" },
                    "key": #{ "type": "string" },
                    "kind": #{
                        "type": "string",
                        "enum": ["cull_test", "drop_seam", "kernel_extract", "host_dedup", "simplify"],
                    },
                    "remedy": #{
                        "type": "string",
                        "enum": ["delete", "replace", "extract", "inline", "refactor"],
                    },
                    "title": #{ "type": "string" },
                    "spec": #{ "type": "string" },
                    "paths": #{
                        "type": "array",
                        "items": #{ "type": "string" },
                        "maxItems": 8,
                    },
                    "signal": #{ "type": "string" },
                },
            },
        },
    },
};

let act_schema = #{
    "type": "object",
    "required": ["ok", "summary", "changed_paths", "deleted_tests", "added_tests", "notes", "keys_touched"],
    "properties": #{
        "ok": #{ "type": "boolean" },
        "summary": #{ "type": "string" },
        "changed_paths": #{
            "type": "array",
            "items": #{ "type": "string" },
            "maxItems": 40,
        },
        "deleted_tests": #{
            "type": "array",
            "items": #{ "type": "string" },
            "maxItems": 80,
        },
        "added_tests": #{
            "type": "array",
            "items": #{ "type": "string" },
            "maxItems": 40,
        },
        "keys_touched": #{
            "type": "array",
            "items": #{ "type": "string" },
            "maxItems": 40,
        },
        "notes": #{ "type": "string" },
    },
};

let verify_schema = #{
    "type": "object",
    "required": ["passed", "command", "summary"],
    "properties": #{
        "passed": #{ "type": "boolean" },
        "command": #{ "type": "string" },
        "summary": #{ "type": "string" },
        "failed_targets": #{
            "type": "array",
            "items": #{ "type": "string" },
            "maxItems": 20,
        },
    },
};

// --- helpers -----------------------------------------------------------------

fn clamp_int(v, lo, hi, fallback) {
    if type_of(v) != "i64" && type_of(v) != "f64" && type_of(v) != "i32" {
        return fallback;
    }
    let n = v;
    if n < lo { n = lo; }
    if hi >= lo && n > hi { n = hi; }
    n
}

fn at_least_int(v, lo, fallback) {
    clamp_int(v, lo, lo - 1, fallback)
}

fn as_bool(v, fallback) {
    if type_of(v) == "bool" { v } else { fallback }
}

fn as_str(v, fallback) {
    if type_of(v) == "string" { v } else { fallback }
}

fn trimmed(s) {
    if type_of(s) != "string" { return ""; }
    s.trim();
    s
}

fn kind_rank(kind) {
    if kind == "cull_test" { return 0; }
    if kind == "drop_seam" { return 1; }
    if kind == "kernel_extract" { return 2; }
    if kind == "host_dedup" { return 3; }
    if kind == "simplify" { return 4; }
    9
}

fn focus_allows(focus, kind) {
    if focus == "all" { return true; }
    if focus == "tests" { return kind == "cull_test"; }
    if focus == "seams" { return kind == "drop_seam"; }
    if focus == "kernel" { return kind == "kernel_extract"; }
    if focus == "hosts" { return kind == "host_dedup"; }
    if focus == "simplify" { return kind == "simplify"; }
    true
}

fn finding_has_valid_remedy(f) {
    if f == () { return false; }
    let remedy = as_str(f.remedy, "");
    if remedy == "delete" { return true; }
    if remedy == "extract" { return true; }
    if remedy == "inline" { return true; }
    if remedy == "refactor" { return true; }
    if remedy == "replace" {
        let rep = as_str(f.replacement, "");
        if type_of(rep) == "string" {
            rep.trim();
            return rep != "";
        }
        return false;
    }
    false
}

fn finding_has_signal(f) {
    if f == () { return false; }
    let sig = trimmed(as_str(f.signal, ""));
    sig != ""
}

fn slug_signal(sig) {
    // Compact signal for key suffix (no regex in Rhai — truncate only).
    let s = trimmed(sig);
    if s.len() > 48 {
        return s.sub_string(0, 48);
    }
    s
}

fn primary_path(paths) {
    if type_of(paths) != "array" || paths.len() == 0 { return ""; }
    as_str(paths[0], "")
}

fn ensure_key(f) {
    // Prefer agent key; else derive stable key from kind|path|signal.
    let k = trimmed(as_str(f.key, ""));
    if k != "" {
        f.key = k;
        return f;
    }
    let kind = as_str(f.kind, "unknown");
    let path = primary_path(f.paths);
    let sig = slug_signal(as_str(f.signal, as_str(f.title, "")));
    f.key = kind + "|" + path + "|" + sig;
    f
}

fn normalize_repo_path(p) {
    if type_of(p) != "string" { return ""; }
    let s = p;
    let markers = [
        "/tests/",
        "/sudo/",
        "/bookmarklet/",
        "/src/infinite_craft_cli/",
        "/.grok/",
    ];
    for m in markers {
        let idx = -1;
        let i = 0;
        while i + m.len() <= s.len() {
            if s.sub_string(i, m.len()) == m {
                idx = i;
            }
            i += 1;
        }
        if idx >= 0 {
            return s.sub_string(idx + 1, s.len() - (idx + 1));
        }
    }
    s
}

fn path_in_scope(p) {
    let s = normalize_repo_path(p);
    if s == "" { return false; }
    if s == "sudo/craft.sudo" { return true; }
    if s == "src/infinite_craft_cli/cli.py" { return true; }
    if s == "src/infinite_craft_cli/ratelimit.py" { return true; }
    if s == "bookmarklet/trainer.src.mjs" { return true; }
    if s == "tests/conftest.py" { return true; }
    if s == "tests/help_utils.py" { return true; }
    if s.len() >= 6 {
        let head = s.sub_string(0, 6);
        if head == "tests/" { return true; }
    }
    if s.len() >= 5 {
        let head5 = s.sub_string(0, 5);
        if head5 == "sudo/" { return true; }
    }
    if s.len() >= 11 {
        let head11 = s.sub_string(0, 11);
        if head11 == "bookmarklet/" { return true; }
    }
    if s.len() >= 22 {
        let head22 = s.sub_string(0, 22);
        if head22 == "src/infinite_craft_cli/" { return true; }
    }
    false
}

fn filter_paths(paths) {
    let out = [];
    if type_of(paths) != "array" { return out; }
    for p in paths {
        let n = normalize_repo_path(p);
        if path_in_scope(n) { out.push(n); }
    }
    out
}

fn has_py_and_js_paths(paths) {
    // kernel_extract / host_dedup must cite both hosts (or craft.sudo + both).
    let py = false;
    let js = false;
    let sudo = false;
    if type_of(paths) != "array" { return false; }
    for p in paths {
        let s = as_str(p, "");
        if s.len() >= 22 && s.sub_string(0, 22) == "src/infinite_craft_cli/" { py = true; }
        if s == "src/infinite_craft_cli/cli.py" { py = true; }
        if s == "src/infinite_craft_cli/ratelimit.py" { py = true; }
        if s.len() >= 11 && s.sub_string(0, 11) == "bookmarklet/" { js = true; }
        if s == "sudo/craft.sudo" { sudo = true; }
    }
    // dual-host pure: both hosts, or one host + kernel when extracting
    if py && js { return true; }
    if sudo && (py || js) { return true; }
    false
}

fn finding_passes_class_gate(f) {
    // Closed problem classes — reject open polish that cannot shrink residual.
    let kind = as_str(f.kind, "");
    let paths = f.paths;
    if kind == "kernel_extract" {
        // Must cite dual-host (or kernel+host) pure duplication — not "extract this string".
        if !has_py_and_js_paths(paths) { return false; }
        return true;
    }
    if kind == "host_dedup" {
        if !has_py_and_js_paths(paths) { return false; }
        return true;
    }
    // cull_test / drop_seam / simplify: path filter already applied
    true
}

fn key_in_list(list, key) {
    if type_of(list) != "array" { return false; }
    for x in list {
        if as_str(x, "") == key { return true; }
    }
    false
}

fn finding_by_key(arr, key) {
    if type_of(arr) != "array" { return (); }
    for f in arr {
        if as_str(f.key, "") == key { return f; }
    }
    ()
}

fn remove_finding_key(arr, key) {
    let out = [];
    if type_of(arr) != "array" { return out; }
    for f in arr {
        if as_str(f.key, "") != key { out.push(f); }
    }
    out
}

fn merge_act_output(out) {
    let local_changed = [];
    let local_deleted = [];
    let local_added = [];
    let keys_touched = [];
    let notes = "";
    let summary = "";
    let ok = false;
    if out != () {
        ok = as_bool(out.ok, false);
        summary = as_str(out.summary, "");
        notes = as_str(out.notes, "");
        if out.changed_paths != () {
            for p in filter_paths(out.changed_paths) { local_changed.push(p); }
        }
        if out.deleted_tests != () {
            for t in out.deleted_tests {
                if type_of(t) == "string" { local_deleted.push(t); }
            }
        }
        if out.added_tests != () {
            for t in out.added_tests {
                if type_of(t) == "string" { local_added.push(t); }
            }
        }
        if out.keys_touched != () {
            for k in out.keys_touched {
                if type_of(k) == "string" { keys_touched.push(k); }
            }
        }
    }
    #{
        ok: ok,
        summary: summary,
        notes: notes,
        changed: local_changed,
        deleted: local_deleted,
        added: local_added,
        keys_touched: keys_touched,
    }
}

fn build_act_opts(label, use_grok_flag, schema) {
    let opts = #{
        label: label,
        capability_mode: "all",
        output_schema: schema,
    };
    if use_grok_flag {
        opts.agent_type = "fable-advisor:grok-implementer";
    }
    opts
}

fn build_verify_opts(label, schema) {
    #{
        label: label,
        capability_mode: "all",
        output_schema: schema,
    }
}

fn doctrine_text() {
    let d = "";
    d += "PROJECT DOCTRINE (non-negotiable):\n";
    d += "1. Maximize business logic in sudocode (sudo/craft.sudo). Pure rules, classification, ";
    d += "thresholds, confirm policy, rate chrome math, queue accept decisions belong there.\n";
    d += "2. Minimize duplicated logic between Python (src/infinite_craft_cli/) and JS ";
    d += "(bookmarklet/trainer.src.mjs). Hosts own I/O only: TTY/DOM, async workers, HTTP, clocks, deques, paint.\n";
    d += "3. Tests must justify their presence. Prefer behavioral harnesses ";
    d += "(tests/test_interactive_ux.py, tests/parity, kernel lockstep).\n";
    d += "4. Bad tests: re-assert source; poke private globals as the oracle; force seams; ";
    d += "duplicate stronger coverage; break on pure refactors. Remedy is delete (coverage exists) ";
    d += "OR replace (add a better behavioral test, then remove the bad one). Never delete-only when ";
    d += "behavior would go untested.\n";
    d += "5. After bad tests leave, DELETE seams they forced (thin wrappers, aliases, getattr soup).\n";
    d += "Findings are work orders only — never inventory rows about things to leave alone.\n";
    d += "6. SIMPLIFY only finite defects: dead duals, pure re-exports, single-use wrappers — not endless polish.\n";
    d += "7. Do NOT fully split cli.py into modules in this workflow unless an action explicitly says so.\n";
    d += "8. Do NOT commit or push unless the plan explicitly includes a commit action (default: no commit).\n";
    d += "9. KEEP high-value behavior: dual-queue UX harnesses, confirm FSM harnesses, target set/show/clear, ";
    d += "ratelimit chrome math, noninteractive CLI, parity lockstep, kernel tests.\n";
    d += "10. CONVERGENCE: empty findings is success. Do not invent work to fill a quota. ";
    d += "kernel_extract / host_dedup require the SAME pure rule on BOTH hosts (cite both paths). ";
    d += "Do NOT propose extracting message templates / prose / copy into the kernel unless both hosts ";
    d += "already implement the same pure decision with duplicated logic.\n";
    d
}

fn default_verify_cmd() {
    // ONLY package globs — never named //pkg:target that a cull can delete.
    let c = "bazel test ";
    c += "//sudo/... ";
    c += "//tests/... ";
    c += "//tests/parity/... ";
    c += "//bookmarklet/... ";
    c += "&& bazel build //bookmarklet:trainer_js";
    c
}

fn invent_rules_blob() {
    let p = "";
    p += "INVENT RULES (new findings only — residual is handled separately):\n";
    p += "- Prefer findings=[] after a real inspection. Empty is success, not failure.\n";
    p += "- Emit ONLY high-confidence finite defects you want fixed NOW.\n";
    p += "- Every finding MUST have: key (stable, kind|path|signal), signal (greppable substring that ";
    p += "must DISAPPEAR when fixed), paths (repo-relative), remedy, replacement (\"\" unless replace).\n";
    p += "- Do NOT invent inventory, polish, or 'for completeness' rows.\n";
    p += "- Do NOT re-emit keys listed as RESOLVED or already OPEN.\n";
    p += "- kernel_extract / host_dedup: cite BOTH Python and JS paths (or craft.sudo + host). ";
    p += "Reject single-host message-template farming.\n";
    p += "- Cap yourself: few findings beat many weak ones.\n";
    p
}

// --- args --------------------------------------------------------------------

let max_rounds = 3;
let max_actions = 0;
// Invent budget per round (residual recheck is unlimited). Default 5.
let max_new = 5;
let dry_run = false;
let approve_each = false;
let focus = "all";
let base_ref = "HEAD";
let verify_cmd = default_verify_cmd();
let act_mode = "sequential";
let use_grok = false;

if args != () {
    if args.max_rounds != () { max_rounds = at_least_int(args.max_rounds, 1, 3); }
    if args.max_actions_per_round != () {
        max_actions = at_least_int(args.max_actions_per_round, 0, 0);
    }
    if args.max_new_findings_per_round != () {
        max_new = at_least_int(args.max_new_findings_per_round, 0, 5);
    }
    if args.dry_run != () { dry_run = as_bool(args.dry_run, false); }
    if args.approve_each_round != () { approve_each = as_bool(args.approve_each_round, false); }
    if args.focus != () { focus = as_str(args.focus, "all"); }
    if args.base_ref != () { base_ref = as_str(args.base_ref, "HEAD"); }
    if args.verify_cmd != () {
        let vc = as_str(args.verify_cmd, "");
        if trimmed(vc) != "" { verify_cmd = vc; }
    }
    if args.act_mode != () { act_mode = as_str(args.act_mode, "sequential"); }
    if args.use_grok_implementer != () { use_grok = as_bool(args.use_grok_implementer, false); }
}

if focus != "all" && focus != "tests" && focus != "seams"
    && focus != "kernel" && focus != "hosts" && focus != "simplify" {
    focus = "all";
}
if act_mode != "sequential" && act_mode != "batch" {
    act_mode = "sequential";
}

let max_actions_label = "unlimited";
if max_actions > 0 { max_actions_label = max_actions.to_string(); }
log("cleanup-loop start: max_rounds=" + max_rounds.to_string()
    + " max_actions_per_round=" + max_actions_label
    + " max_new_findings_per_round=" + max_new.to_string()
    + " focus=" + focus
    + " dry_run=" + dry_run.to_string()
    + " act_mode=" + act_mode
    + " use_grok=" + use_grok.to_string());

// --- loop state (residual ledger) --------------------------------------------

let round = 0;
let empty_streak = 0;
let no_shrink_streak = 0;
let invent_allowed = true;
let last_fp = "";
let history = [];
let all_deleted_tests = [];
let all_added_tests = [];
let all_changed = [];
// Open residual: finding maps with key/signal/paths/...
let open_residual = [];
// Resolved keys (never re-admit invent for these)
let resolved_keys = [];
let stop_reason = "";

while round < max_rounds {
    round += 1;
    let round_label = "round-" + round.to_string();
    log("--- " + round_label + " residual_open=" + open_residual.len().to_string()
        + " invent_allowed=" + invent_allowed.to_string()
        + " no_shrink_streak=" + no_shrink_streak.to_string() + " ---");

    phase("Discover");
    let doctrine = doctrine_text();

    let scope_blob = "";
    scope_blob += "In-scope paths only (repo-RELATIVE, never absolute):\n";
    scope_blob += "  sudo/craft.sudo, src/infinite_craft_cli/**, bookmarklet/**, tests/**\n";
    scope_blob += "Always set finding.paths to relative paths like \"bookmarklet/trainer.src.mjs\" ";
    scope_blob += "or \"src/infinite_craft_cli/cli.py\" — NOT /Users/... absolute paths.\n";
    scope_blob += "Focus filter for this run: " + focus + "\n";
    scope_blob += "You MUST use tools (grep, read_file, list_dir, git).\n";

    // ── Residual recheck (always when ledger non-empty) ─────────────────────
    let still_open = [];
    let recheck_cleared = 0;
    if open_residual.len() > 0 {
        let b0 = budget();
        if b0.remaining < 3 {
            log("budget low before residual recheck; stopping");
            stop_reason = "budget";
            break;
        }
        let recheck_prompt = "";
        recheck_prompt += doctrine + "\n" + scope_blob;
        recheck_prompt += "RESIDUAL RECHECK only. Do NOT invent new findings.\n";
        recheck_prompt += "For each OPEN item, grep/read its paths for its signal.\n";
        recheck_prompt += "still_present=true only if the greppable signal is still in those files.\n";
        recheck_prompt += "still_present=false if the defect is gone (signal absent / test deleted / seam gone).\n";
        recheck_prompt += "evidence: short quote or 'not found' + tool used.\n";
        recheck_prompt += "OPEN RESIDUAL JSON:\n";
        recheck_prompt += json_encode(open_residual) + "\n";
        recheck_prompt += "Return one results[] entry per open key.\n";

        let rr = agent(recheck_prompt, #{
            label: round_label + ":residual-recheck",
            capability_mode: "read-only",
            output_schema: residual_schema,
        });

        // Default: keep all open if recheck fails (fail closed on residual).
        let present_map_keys = [];
        let gone_map_keys = [];
        if rr != () && rr.success && rr.output != () && rr.output.results != () {
            for row in rr.output.results {
                if row == () { continue; }
                let rk = as_str(row.key, "");
                if rk == "" { continue; }
                if as_bool(row.still_present, true) {
                    present_map_keys.push(rk);
                } else {
                    gone_map_keys.push(rk);
                }
            }
        } else {
            log("residual recheck failed — keeping all open keys");
            for f in open_residual {
                present_map_keys.push(as_str(f.key, ""));
            }
        }

        for f in open_residual {
            let k = as_str(f.key, "");
            if key_in_list(gone_map_keys, k) {
                recheck_cleared += 1;
                if !key_in_list(resolved_keys, k) { resolved_keys.push(k); }
                log("residual cleared (pre-act): " + k);
            } else {
                still_open.push(f);
            }
        }
        open_residual = still_open;
        log("residual recheck: still_open=" + open_residual.len().to_string()
            + " cleared=" + recheck_cleared.to_string());
    }

    let open_after_recheck = open_residual.len();

    // ── Invent scanners (capped; skipped when invent not allowed) ───────────
    let invented = [];
    let raw_invent = 0;
    let dropped_invent = 0;

    if invent_allowed && max_new > 0 {
        let invent_jobs = [];
        let resolved_json = json_encode(resolved_keys);
        let open_keys = [];
        for f in open_residual { open_keys.push(as_str(f.key, "")); }
        let open_keys_json = json_encode(open_keys);
        let ledger_blob = "";
        ledger_blob += "RESOLVED keys (do not re-emit): " + resolved_json + "\n";
        ledger_blob += "OPEN keys (already on residual; do not re-emit): " + open_keys_json + "\n";
        ledger_blob += invent_rules_blob();

        if focus == "all" || focus == "tests" {
            let p = "";
            p += doctrine + "\n" + scope_blob + ledger_blob;
            p += "DIMENSION: cull_test\n";
            p += "Audit tests/ for low-value or implementation-asserting tests.\n";
            p += "Prioritize: source identifier asserts; private _command_queue/_chrome_* oracles; ";
            p += "seams-only pins; duplicates of test_interactive_ux or tests/parity.\n";
            p += "Read at least samples of: tests/test_interactive.py, tests/test_interactive_ux.py, ";
            p += "tests/test_formatting.py, tests/test_bulk.py, tests/test_cli.py, tests/parity/.\n";
            p += "remedy=delete with named remaining coverage, or remedy=replace with replacement brief.\n";
            p += "signal = greppable test name or unique assertion fragment that must leave the suite.\n";
            p += "Empty findings if suite is fine. Return dimension=\"tests\".\n";
            invent_jobs.push(#{
                prompt: p,
                label: round_label + ":scan-tests",
                capability_mode: "read-only",
                output_schema: findings_schema,
            });
        }
        if focus == "all" || focus == "seams" {
            let p = "";
            p += doctrine + "\n" + scope_blob + ledger_blob;
            p += "DIMENSION: drop_seam\n";
            p += "Finite seams only: dead aliases, pure re-export wrappers of craft.*, getattr soup ";
            p += "only for tests, deprecated dual APIs.\n";
            p += "signal = symbol name that must disappear (e.g. function def line fragment).\n";
            p += "If a seam must stay for harnesses, emit nothing. Return dimension=\"seams\".\n";
            invent_jobs.push(#{
                prompt: p,
                label: round_label + ":scan-seams",
                capability_mode: "read-only",
                output_schema: findings_schema,
            });
        }
        if focus == "all" || focus == "kernel" {
            let p = "";
            p += doctrine + "\n" + scope_blob + ledger_blob;
            p += "DIMENSION: kernel_extract\n";
            p += "ONLY pure logic duplicated on BOTH hosts (or host clone of something that belongs in ";
            p += "craft.sudo). Cite BOTH py and js paths (and craft.sudo if adding API).\n";
            p += "FORBIDDEN: message templates, prose, copy-only 'extract this string', single-host polish.\n";
            p += "signal = greppable host fragment that should leave after extract.\n";
            p += "If nothing dual-host remains, findings=[]. Return dimension=\"kernel\".\n";
            invent_jobs.push(#{
                prompt: p,
                label: round_label + ":scan-kernel",
                capability_mode: "read-only",
                output_schema: findings_schema,
            });
        }
        if focus == "all" || focus == "hosts" || focus == "simplify" {
            let p = "";
            p += doctrine + "\n" + scope_blob + ledger_blob;
            p += "DIMENSION: host_dedup / simplify\n";
            p += "Cloned control flow across py+js, or finite dead duals/wrappers on one host.\n";
            p += "host_dedup MUST cite both hosts. simplify: dead duals / pure re-exports only.\n";
            p += "Do not propose full cli.py splits. Empty is success. Return dimension=\"hosts\".\n";
            invent_jobs.push(#{
                prompt: p,
                label: round_label + ":scan-hosts",
                capability_mode: "read-only",
                output_schema: findings_schema,
            });
        }

        if invent_jobs.len() > 0 {
            let b1 = budget();
            if b1.remaining < invent_jobs.len() + 5 {
                log("budget low before invent scan; skipping invent");
            } else {
                let scan_results = parallel(invent_jobs);
                let si = 0;
                for r in scan_results {
                    if r != () && r.success && r.output != () && r.output.findings != () {
                        for f in r.output.findings {
                            if f == () { continue; }
                            raw_invent += 1;
                            let kind = as_str(f.kind, "");
                            if !focus_allows(focus, kind) {
                                dropped_invent += 1;
                                continue;
                            }
                            if !finding_has_valid_remedy(f) {
                                dropped_invent += 1;
                                continue;
                            }
                            if !finding_has_signal(f) {
                                dropped_invent += 1;
                                log("drop invent (no signal): " + as_str(f.title, "?"));
                                continue;
                            }
                            let paths = filter_paths(f.paths);
                            if paths.len() == 0 && kind == "cull_test" { paths = ["tests/"]; }
                            if paths.len() == 0 {
                                dropped_invent += 1;
                                continue;
                            }
                            f.paths = paths;
                            f = ensure_key(f);
                            let fk = as_str(f.key, "");
                            if key_in_list(resolved_keys, fk) {
                                dropped_invent += 1;
                                continue;
                            }
                            if finding_by_key(open_residual, fk) != () {
                                dropped_invent += 1;
                                continue;
                            }
                            if finding_by_key(invented, fk) != () {
                                dropped_invent += 1;
                                continue;
                            }
                            // High confidence preferred; drop low on invent
                            let conf = as_str(f.confidence, "medium");
                            if conf == "low" {
                                dropped_invent += 1;
                                continue;
                            }
                            if !finding_passes_class_gate(f) {
                                dropped_invent += 1;
                                log("drop invent (class gate): " + fk);
                                continue;
                            }
                            invented.push(f);
                        }
                    } else {
                        log("invent scanner slot " + si.to_string() + " failed or empty");
                    }
                    si += 1;
                }
            }
        }

        // Cap invent admissions
        if invented.len() > max_new {
            log("invent cap: truncating " + invented.len().to_string()
                + " → " + max_new.to_string());
            let capped = [];
            let i = 0;
            while i < max_new {
                capped.push(invented[i]);
                i += 1;
            }
            invented = capped;
        }

        // Admit invent into residual
        for f in invented {
            open_residual.push(f);
        }
        log("invent: admitted=" + invented.len().to_string()
            + " raw=" + raw_invent.to_string()
            + " dropped=" + dropped_invent.to_string());
    } else {
        log("invent skipped (allowed=" + invent_allowed.to_string()
            + " max_new=" + max_new.to_string() + ")");
    }

    let work = open_residual;
    log("work set size: " + work.len().to_string()
        + " (open_after_recheck=" + open_after_recheck.to_string()
        + " + invent this round)");

    // ── Plan ────────────────────────────────────────────────────────────────
    phase("Plan");

    let findings_json = json_encode(work);
    let plan_prompt = "";
    plan_prompt += doctrine + "\n";
    plan_prompt += "You are the planner for round " + round.to_string() + " of cleanup-loop.\n";
    plan_prompt += "Work set is the residual ledger (rechecked + any newly admitted findings).\n";
    plan_prompt += "Carry each finding.key and finding.signal onto actions. Do not invent new keys.\n";
    plan_prompt += "Every work-set item should become an action or be skipped with reason (already gone).\n";
    plan_prompt += "For remedy=replace: spec must include remove + full replacement from finding.replacement.\n";
    plan_prompt += "For remedy=delete: name remaining coverage.\n";
    plan_prompt += "ORDER: cull_test, drop_seam, kernel_extract, host_dedup, simplify; high conf first.\n";
    plan_prompt += "Set done=true only if work set empty OR nothing safe to do.\n";
    plan_prompt += "Prefer empty actions over unsafe actions.\n";
    if max_actions > 0 {
        plan_prompt += "DEBUG CAP: at most " + max_actions.to_string() + " actions.\n";
    }
    plan_prompt += "WORK SET JSON:\n";
    plan_prompt += findings_json + "\n";
    plan_prompt += "If empty: done=true, actions=[].\n";

    let plan_r = agent(plan_prompt, #{
        label: round_label + ":plan",
        capability_mode: "read-only",
        output_schema: plan_schema,
    });

    let actions = [];
    let plan_done = false;
    let plan_rationale = "";
    if plan_r != () && plan_r.success && plan_r.output != () {
        plan_done = as_bool(plan_r.output.done, false);
        plan_rationale = as_str(plan_r.output.rationale, "");
        if plan_r.output.actions != () {
            for a in plan_r.output.actions {
                if a == () { continue; }
                let kind = as_str(a.kind, "");
                if !focus_allows(focus, kind) { continue; }
                let paths = filter_paths(a.paths);
                if paths.len() == 0 && kind == "cull_test" { paths = ["tests/"]; }
                if paths.len() == 0 { continue; }
                a.paths = paths;
                // Fill key/signal from residual if planner omitted
                let ak = trimmed(as_str(a.key, ""));
                if ak == "" {
                    let match_f = finding_by_key(work, as_str(a.id, ""));
                    if match_f == () {
                        // try title match on work set
                        for f in work {
                            if as_str(f.title, "") == as_str(a.title, "") {
                                match_f = f;
                                break;
                            }
                        }
                    }
                    if match_f != () {
                        a.key = as_str(match_f.key, "");
                        if trimmed(as_str(a.signal, "")) == "" {
                            a.signal = as_str(match_f.signal, "");
                        }
                    }
                }
                if trimmed(as_str(a.key, "")) == "" { continue; }
                if trimmed(as_str(a.signal, "")) == "" {
                    let f2 = finding_by_key(work, as_str(a.key, ""));
                    if f2 != () { a.signal = as_str(f2.signal, ""); }
                }
                if trimmed(as_str(a.signal, "")) == "" { continue; }
                actions.push(a);
            }
        }
    } else {
        log("planner failed; treating round as empty");
        plan_done = true;
        plan_rationale = "planner failed";
    }

    // Sort by kind rank; optional debug action cap
    let ordered = [];
    let ranks = [0, 1, 2, 3, 4];
    for rk in ranks {
        for a in actions {
            if kind_rank(as_str(a.kind, "")) == rk {
                if max_actions > 0 && ordered.len() >= max_actions {
                    // truncate
                } else {
                    ordered.push(a);
                }
            }
        }
    }
    if max_actions > 0 && actions.len() > max_actions {
        log("DEBUG: truncated plan from " + actions.len().to_string()
            + " to " + max_actions.to_string());
    }
    actions = ordered;

    let actions_fp = fingerprint(json_encode(actions));
    log("planned actions: " + actions.len().to_string() + " fp=" + actions_fp);
    if plan_rationale != "" { log("plan: " + plan_rationale); }

    // Empty work / empty plan only when there is nothing to act on.
    // plan_done with non-empty actions is ignored (act on the actions).
    if work.len() == 0 || actions.len() == 0 {
        empty_streak += 1;
        history.push(#{
            round: round,
            actions: 0,
            dry: true,
            rationale: plan_rationale,
            residual_open: open_residual.len(),
            invent_admitted: invented.len(),
            invent_allowed: invent_allowed,
            plan_done: plan_done,
        });
        if empty_streak >= 2 {
            log("two empty rounds — residual dry; done");
            stop_reason = "dry";
            break;
        }
        log("empty work/plan this round");
        if dry_run {
            let report = "";
            report += "# cleanup-loop dry run (round " + round.to_string() + ")\n\n";
            report += "No actions planned. Residual empty or nothing safe.\n\n";
            report += "Rationale: " + plan_rationale + "\n";
            let path = write_scratch_file("dry-run-round-" + round.to_string() + ".md", report);
            complete(#{
                summary: "dry_run: no actions planned",
                history: history,
                report: path,
                residual: open_residual,
                resolved_keys: resolved_keys,
            });
        }
        continue;
    } else {
        empty_streak = 0;
    }

    if last_fp != "" && actions_fp == last_fp {
        log("action fingerprint stalled vs previous round — stop");
        history.push(#{
            round: round,
            actions: actions.len(),
            stalled: true,
            residual_open: open_residual.len(),
        });
        stop_reason = "stalled_fp";
        break;
    }
    last_fp = actions_fp;

    if dry_run {
        let report = "";
        report += "# cleanup-loop dry run (round " + round.to_string() + ")\n\n";
        report += plan_rationale + "\n\n";
        report += "## Residual open: " + open_residual.len().to_string() + "\n";
        report += "## Planned actions (" + actions.len().to_string() + ")\n\n";
        for a in actions {
            report += "- **" + as_str(a.key, "") + "** " + as_str(a.kind, "") + ": "
                + as_str(a.title, "") + "\n";
            report += "  - signal: `" + as_str(a.signal, "") + "`\n";
            report += "  - spec: " + as_str(a.spec, "") + "\n";
        }
        let path = write_scratch_file("dry-run-round-" + round.to_string() + ".md", report);
        history.push(#{
            round: round,
            dry_run: true,
            actions: actions.len(),
            residual_open: open_residual.len(),
            report: path,
        });
        complete(#{
            summary: "dry_run: " + actions.len().to_string() + " actions planned",
            history: history,
            report: path,
            actions: actions,
            residual: open_residual,
            resolved_keys: resolved_keys,
            focus: focus,
            dry_run: true,
        });
    }

    if approve_each {
        let msg = "Round " + round.to_string() + ": implement " + actions.len().to_string()
            + " actions (residual " + open_residual.len().to_string() + ")? Resume to continue.";
        await_user("user", msg);
        empty_streak = 0;
    }

    // Snapshot open size before act for shrink detection (ledger after invent)
    let open_before_act = open_residual.len();

    // ── Act ─────────────────────────────────────────────────────────────────
    phase("Act");

    let plan_path = write_scratch_file(
        "plan-round-" + round.to_string() + ".json",
        json_encode(#{
            round: round,
            focus: focus,
            rationale: plan_rationale,
            actions: actions,
            residual_open: open_before_act,
            invent_allowed: invent_allowed,
        }),
    );
    log("wrote plan " + plan_path);

    let need = actions.len() + 6;
    if act_mode == "batch" { need = 8; }
    let b = budget();
    if b.remaining < need {
        log("budget too low to act+verify+proof (need ~" + need.to_string() + ")");
        stop_reason = "budget";
        break;
    }

    let act_rules = "";
    act_rules += doctrine + "\n";
    act_rules += "You are the IMPLEMENTER for infinite-craft-cli cleanup-loop.\n";
    act_rules += "Workspace: sudo/craft.sudo, tests/, src/infinite_craft_cli/, bookmarklet/.\n";
    act_rules += "CRITICAL RULES:\n";
    act_rules += "1. USE tools: read_file, grep, search_replace/write, run_terminal_command.\n";
    act_rules += "2. Apply ONLY the assigned action(s). No freestyle culls/restores.\n";
    act_rules += "3. Dirty git tree is NORMAL. Do NOT git restore/checkout. Do NOT commit/push.\n";
    act_rules += "4. If already applied, ok=true notes=already applied.\n";
    act_rules += "5. py_compile touched Python. Do not run full bazel here.\n";
    act_rules += "6. remedy=delete: remove only. remedy=replace: ADD replacement then remove bad.\n";
    act_rules += "7. Clear the action.signal greppable fragment from the codebase (that is the definition of done).\n";
    act_rules += "8. keys_touched must list the action.key values you applied.\n";
    act_rules += "9. List deleted_tests / added_tests when relevant.\n";

    let changed_paths = [];
    let deleted_tests = [];
    let added_tests = [];
    let act_notes = "";
    let ok_count = 0;
    let fail_count = 0;
    let failed_actions = [];
    let claimed_keys = [];

    if act_mode == "batch" {
        let act_prompt = act_rules;
        act_prompt += "Implement ALL actions IN ORDER.\n";
        act_prompt += "Plan: " + plan_path + "\n";
        act_prompt += "ACTIONS:\n" + json_encode(actions) + "\n";
        act_prompt += "ok=true only if every action applied or already present AND signals cleared.\n";
        let act_r = agent(act_prompt, build_act_opts(round_label + ":act-batch", use_grok, act_schema));
        let parsed = #{ ok: false, summary: "batch act failed", notes: "", changed: [], deleted: [], added: [], keys_touched: [] };
        if act_r != () && act_r.success && act_r.output != () {
            parsed = merge_act_output(act_r.output);
        }
        act_notes = parsed.notes;
        for p in parsed.changed { changed_paths.push(p); all_changed.push(p); }
        for t in parsed.deleted { deleted_tests.push(t); all_deleted_tests.push(t); }
        for t in parsed.added { added_tests.push(t); all_added_tests.push(t); }
        for k in parsed.keys_touched { claimed_keys.push(k); }
        if parsed.ok {
            ok_count = actions.len();
            for a in actions { claimed_keys.push(as_str(a.key, "")); }
        } else {
            fail_count = actions.len();
            for a in actions { failed_actions.push(a); }
        }
    } else {
        let ai = 0;
        for a in actions {
            ai += 1;
            let act_prompt = act_rules;
            act_prompt += "SINGLE ACTION " + ai.to_string() + " of " + actions.len().to_string() + ".\n";
            act_prompt += "Implement EXACTLY:\n" + json_encode(a) + "\n";
            act_prompt += "Done means signal `" + as_str(a.signal, "") + "` is gone from paths.\n";
            act_prompt += "keys_touched: [\"" + as_str(a.key, "") + "\"] when applied.\n";
            let act_r = agent(
                act_prompt,
                build_act_opts(round_label + ":act-" + ai.to_string(), use_grok, act_schema),
            );
            let parsed = #{ ok: false, summary: "act failed", notes: "", changed: [], deleted: [], added: [], keys_touched: [] };
            if act_r != () && act_r.success && act_r.output != () {
                parsed = merge_act_output(act_r.output);
            } else {
                parsed.notes = "agent failure or schema miss";
            }
            act_notes = act_notes + " | [" + ai.to_string() + "] " + parsed.notes;
            for p in parsed.changed { changed_paths.push(p); all_changed.push(p); }
            for t in parsed.deleted { deleted_tests.push(t); all_deleted_tests.push(t); }
            for t in parsed.added { added_tests.push(t); all_added_tests.push(t); }
            if parsed.ok {
                ok_count += 1;
                claimed_keys.push(as_str(a.key, ""));
                log("act " + ai.to_string() + " ok: " + parsed.summary);
            } else {
                fail_count += 1;
                failed_actions.push(a);
                log("act " + ai.to_string() + " FAIL: " + parsed.summary);
            }
        }
    }

    if failed_actions.len() > 0 {
        log("retry " + failed_actions.len().to_string() + " failed action(s)");
        let retry_prompt = act_rules;
        retry_prompt += "RETRY. Apply these failed actions. Clear each signal.\n";
        retry_prompt += json_encode(failed_actions) + "\n";
        let retry_r = agent(retry_prompt, build_act_opts(round_label + ":act-retry", false, act_schema));
        if retry_r != () && retry_r.success && retry_r.output != () {
            let parsed = merge_act_output(retry_r.output);
            act_notes = act_notes + " | retry: " + parsed.notes;
            for p in parsed.changed { changed_paths.push(p); all_changed.push(p); }
            for t in parsed.deleted { deleted_tests.push(t); all_deleted_tests.push(t); }
            for t in parsed.added { added_tests.push(t); all_added_tests.push(t); }
            if parsed.ok {
                ok_count += failed_actions.len();
                fail_count = 0;
                for a in failed_actions { claimed_keys.push(as_str(a.key, "")); }
                failed_actions = [];
                log("retry ok: " + parsed.summary);
            }
        }
    }

    let act_ok = fail_count == 0 && ok_count > 0;
    let any_progress = ok_count > 0 || changed_paths.len() > 0;
    let act_summary = "ok=" + ok_count.to_string()
        + " fail=" + fail_count.to_string()
        + " of " + actions.len().to_string();
    log("act summary: " + act_summary);

    if !any_progress {
        history.push(#{
            round: round,
            act_ok: false,
            summary: act_summary,
            residual_open: open_residual.len(),
            notes: act_notes,
        });
        empty_streak += 1;
        invent_allowed = false;
        no_shrink_streak += 1;
        if empty_streak >= 2 || no_shrink_streak >= 2 {
            stop_reason = "no_progress";
            break;
        }
        await_user(
            "no_progress",
            "cleanup-loop act made no progress. Resume for residual-only next round, or stop.",
        );
        continue;
    }

    // ── Verify bazel ────────────────────────────────────────────────────────
    phase("Verify");

    let v_prompt = "";
    v_prompt += "Run this verification command from the repo root and report results.\n";
    v_prompt += "You MUST use the shell/terminal tool and wait for exit code.\n";
    v_prompt += "Command:\n" + verify_cmd + "\n";
    v_prompt += "Set passed=true only if exit code is 0.\n";
    v_prompt += "Do not modify code in this step.\n";

    let v_r = agent(v_prompt, build_verify_opts(round_label + ":verify", verify_schema));
    let passed = false;
    let v_summary = "verify missing";
    if v_r != () && v_r.success && v_r.output != () {
        passed = as_bool(v_r.output.passed, false);
        v_summary = as_str(v_r.output.summary, "");
    }

    if !passed {
        log("verify failed — one fix attempt");
        let fix_prompt = act_rules;
        fix_prompt += "Verification FAILED. Minimal fix only. Re-clear action signals if needed.\n";
        fix_prompt += "Verify summary: " + v_summary + "\n";
        fix_prompt += "Actions: " + json_encode(actions) + "\n";
        fix_prompt += "Re-run until green:\n" + verify_cmd + "\n";
        fix_prompt += "Do NOT restore culled low-value tests.\n";
        let fix_r = agent(fix_prompt, build_act_opts(round_label + ":fix", false, act_schema));
        let fix_ok = false;
        if fix_r != () && fix_r.success && fix_r.output != () {
            fix_ok = as_bool(fix_r.output.ok, false);
            if fix_r.output.changed_paths != () {
                for p in filter_paths(fix_r.output.changed_paths) {
                    changed_paths.push(p);
                    all_changed.push(p);
                }
            }
            act_notes = act_notes + " | fix: " + as_str(fix_r.output.notes, "");
        }
        let v2 = agent(v_prompt, #{
            label: round_label + ":verify2",
            capability_mode: "all",
            output_schema: verify_schema,
        });
        passed = false;
        if v2 != () && v2.success && v2.output != () {
            passed = as_bool(v2.output.passed, false);
            v_summary = as_str(v2.output.summary, "");
        }
        if !passed {
            history.push(#{
                round: round,
                act_ok: act_ok,
                verify: false,
                summary: act_summary,
                verify_summary: v_summary,
                fix_ok: fix_ok,
                residual_open: open_residual.len(),
            });
            await_user(
                "verification",
                "cleanup-loop verify still failing. Resume to residual-only next round, or stop.",
            );
            invent_allowed = false;
            empty_streak = 0;
            continue;
        }
    }

    // ── Signal proof (solved = signal gone, not ok=true) ────────────────────
    let proof_items = [];
    for a in actions {
        proof_items.push(#{
            key: as_str(a.key, ""),
            signal: as_str(a.signal, ""),
            paths: a.paths,
            title: as_str(a.title, ""),
            remedy: as_str(a.remedy, ""),
        });
    }
    let proof_prompt = "";
    proof_prompt += "SIGNAL PROOF. Independent of implementer claims.\n";
    proof_prompt += "For each item, grep/read paths for signal. cleared=true ONLY if signal is absent ";
    proof_prompt += "(or for delete of a whole file, file gone / symbol gone).\n";
    proof_prompt += "cleared=false if signal still present. evidence required either way.\n";
    proof_prompt += "ITEMS:\n" + json_encode(proof_items) + "\n";
    proof_prompt += "Use tools. Do not modify code.\n";

    let proof_r = agent(proof_prompt, #{
        label: round_label + ":signal-proof",
        capability_mode: "read-only",
        output_schema: signal_proof_schema,
    });

    let cleared_keys = [];
    let uncleared_keys = [];
    if proof_r != () && proof_r.success && proof_r.output != () && proof_r.output.checks != () {
        for c in proof_r.output.checks {
            if c == () { continue; }
            let ck = as_str(c.key, "");
            if ck == "" { continue; }
            if as_bool(c.cleared, false) {
                cleared_keys.push(ck);
            } else {
                uncleared_keys.push(ck);
            }
        }
    } else {
        // Fail closed: nothing proven cleared
        log("signal proof failed — treating all planned keys as uncleared");
        for a in actions {
            uncleared_keys.push(as_str(a.key, ""));
        }
    }

    // Keys planned but missing from proof → uncleared
    for a in actions {
        let k = as_str(a.key, "");
        if !key_in_list(cleared_keys, k) && !key_in_list(uncleared_keys, k) {
            uncleared_keys.push(k);
        }
    }

    log("signal proof: cleared=" + cleared_keys.len().to_string()
        + " uncleared=" + uncleared_keys.len().to_string());

    for k in cleared_keys {
        open_residual = remove_finding_key(open_residual, k);
        if !key_in_list(resolved_keys, k) { resolved_keys.push(k); }
    }
    // Uncleared stay on residual (ensure present)
    for k in uncleared_keys {
        if finding_by_key(open_residual, k) == () {
            let src = finding_by_key(work, k);
            if src == () {
                for a in actions {
                    if as_str(a.key, "") == k {
                        src = #{
                            key: k,
                            kind: as_str(a.kind, ""),
                            remedy: as_str(a.remedy, ""),
                            title: as_str(a.title, ""),
                            detail: as_str(a.spec, ""),
                            paths: a.paths,
                            signal: as_str(a.signal, ""),
                            confidence: "high",
                            replacement: "",
                            id: as_str(a.id, k),
                        };
                        break;
                    }
                }
            }
            if src != () { open_residual.push(src); }
        }
    }

    let open_after = open_residual.len();
    let shrunk = open_after < open_before_act;
    if shrunk {
        no_shrink_streak = 0;
        invent_allowed = true;
        log("residual shrank " + open_before_act.to_string() + " → " + open_after.to_string()
            + "; invent re-enabled");
    } else {
        no_shrink_streak += 1;
        invent_allowed = false;
        log("residual did NOT shrink (" + open_before_act.to_string() + " → " + open_after.to_string()
            + "); invent OFF; no_shrink_streak=" + no_shrink_streak.to_string());
    }

    phase("Loop");
    history.push(#{
        round: round,
        act_ok: act_ok,
        verify: true,
        actions: actions.len(),
        ok_count: ok_count,
        fail_count: fail_count,
        summary: act_summary,
        deleted_tests: deleted_tests,
        changed_paths: changed_paths,
        notes: act_notes,
        verify_summary: v_summary,
        residual_before: open_before_act,
        residual_after: open_after,
        cleared_keys: cleared_keys,
        uncleared_keys: uncleared_keys,
        invent_admitted: invented.len(),
        invent_allowed_next: invent_allowed,
        shrunk: shrunk,
    });
    log("round " + round.to_string() + " green bazel; residual "
        + open_before_act.to_string() + "→" + open_after.to_string());

    write_scratch_file(
        "ledger-round-" + round.to_string() + ".json",
        json_encode(#{
            open_residual: open_residual,
            resolved_keys: resolved_keys,
            cleared_keys: cleared_keys,
            uncleared_keys: uncleared_keys,
        }),
    );

    if no_shrink_streak >= 2 {
        log("residual unchanged for 2 rounds — stop (invent/solve not converging)");
        stop_reason = "no_shrink";
        break;
    }
    if open_after == 0 {
        empty_streak += 1;
        if empty_streak >= 2 {
            log("residual empty twice — done");
            stop_reason = "dry";
            break;
        }
        // one empty residual: continue once to invent-check
        invent_allowed = true;
    }
}

// --- final report ------------------------------------------------------------

let report = "";
report += "# cleanup-loop report\n\n";
report += "- focus: " + focus + "\n";
report += "- rounds recorded: " + history.len().to_string() + "\n";
report += "- stop_reason: " + stop_reason + "\n";
report += "- residual_open_final: " + open_residual.len().to_string() + "\n";
report += "- resolved_keys: " + resolved_keys.len().to_string() + "\n";
report += "- dry_run: " + dry_run.to_string() + "\n";
report += "- max_new_findings_per_round: " + max_new.to_string() + "\n\n";
report += "## History\n\n";
report += "```json\n" + json_encode(history) + "\n```\n\n";
report += "## Open residual (unsolved)\n\n";
if open_residual.len() == 0 {
    report += "_none_\n";
} else {
    report += "```json\n" + json_encode(open_residual) + "\n```\n";
}
report += "\n## Resolved keys\n\n";
if resolved_keys.len() == 0 {
    report += "_none_\n";
} else {
    for k in resolved_keys {
        report += "- `" + k + "`\n";
    }
}
report += "\n## Deleted tests (claimed)\n\n";
if all_deleted_tests.len() == 0 {
    report += "_none_\n";
} else {
    for t in all_deleted_tests {
        report += "- `" + t + "`\n";
    }
}
report += "\n## Added / replacement tests (claimed)\n\n";
if all_added_tests.len() == 0 {
    report += "_none_\n";
} else {
    for t in all_added_tests {
        report += "- `" + t + "`\n";
    }
}
report += "\n## Changed paths (claimed)\n\n";
if all_changed.len() == 0 {
    report += "_none_\n";
} else {
    for p in all_changed {
        report += "- `" + p + "`\n";
    }
}
report += "\n## Doctrine reminder\n\n";
report += doctrine_text();

let report_path = write_scratch_file("cleanup-loop-report.md", report);
log("wrote " + report_path);

complete(#{
    summary: "cleanup-loop finished: " + stop_reason
        + " after " + history.len().to_string() + " round(s); residual="
        + open_residual.len().to_string(),
    history: history,
    residual: open_residual,
    resolved_keys: resolved_keys,
    deleted_tests: all_deleted_tests,
    added_tests: all_added_tests,
    changed_paths: all_changed,
    report: report_path,
    stop_reason: stop_reason,
    focus: focus,
    dry_run: dry_run,
});
