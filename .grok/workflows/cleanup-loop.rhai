// Continuous quality loop for infinite-craft-cli:
// cull useless tests → drop seams they forced → push pure logic into
// craft.sudo → thin/dedupe Python+JS hosts → simplify → verify → repeat
// until dry, stalled, or max_rounds.
//
// Args (all optional):
//   max_rounds: int (default 3, minimum 1) — runaway brake for number of discover→verify cycles
//   max_actions_per_round: int (optional) — ONLY if set >0, hard-cap planned actions (debug).
//     Default: no cap. Plan must cover all findings worth doing this round.
//   dry_run: bool — discover + plan only, no edits
//   approve_each_round: bool — await_user before implement
//   focus: "all" | "tests" | "seams" | "kernel" | "hosts" | "simplify"
//   base_ref: git ref for optional pre-diff context (default "HEAD")
//   verify_cmd: override bazel verify command (string)
//   act_mode: "sequential" (default, one agent per action) | "batch" (single agent)
//   use_grok_implementer: bool (default false) — prefer session implementer; grok lane
//     previously bailed on dirty trees without applying the plan
//
// Agent budget (workflow tool agent_budget) is the real cost ceiling when many
// actions run sequentially in one round — not max_actions_per_round.

let meta = #{
    name: "cleanup-loop",
    description: "Loop: cull low-value tests, remove seams, consolidate pure logic into craft.sudo, thin PY/JS hosts, verify with bazel",
    when_to_use: "Continuous maintainability pass when hosts duplicate pure rules, tests assert implementation, or craft.sudo should own more business logic",
    phases: [
        #{ title: "Discover", detail: "parallel scanners: tests, seams, kernel candidates, host dup" },
        #{ title: "Plan", detail: "ordered actions covering all findings worth doing" },
        #{ title: "Act", detail: "implement every planned action (or dry-run skip)" },
        #{ title: "Verify", detail: "bazel proof gate; one fix attempt on failure" },
        #{ title: "Loop", detail: "fingerprint progress; stop when dry or stalled" },
    ],
};

// --- schemas -----------------------------------------------------------------

// Findings are WORK ORDERS only. No inventory / "for completeness" rows.
// remedy:
//   delete  — remove the test/code; coverage already exists or is not needed
//   replace — remove bad test/code AND add the behavioral replacement described
//   extract / inline / refactor — host/kernel/seam work (non-test)
// replacement: required non-empty prose when remedy is replace; else "".
let findings_schema = #{
    "type": "object",
    "required": ["findings", "dimension"],
    "properties": #{
        "dimension": #{ "type": "string" },
        "findings": #{
            "type": "array",
            "maxItems": 64,
            "items": #{
                "type": "object",
                "required": ["id", "kind", "remedy", "title", "detail", "paths", "confidence", "replacement"],
                "properties": #{
                    "id": #{ "type": "string" },
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
                        "maxItems": 16,
                    },
                    "confidence": #{ "type": "string", "enum": ["high", "medium", "low"] },
                    "replacement": #{ "type": "string" },
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
            "maxItems": 80,
            "items": #{
                "type": "object",
                "required": ["id", "kind", "remedy", "title", "spec", "paths"],
                "properties": #{
                    "id": #{ "type": "string" },
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
                        "maxItems": 16,
                    },
                },
            },
        },
    },
};

let act_schema = #{
    "type": "object",
    "required": ["ok", "summary", "changed_paths", "deleted_tests", "added_tests", "notes"],
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
    // hi < lo means "no upper bound" (only enforce lo).
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
    // Strict order: tests first so seams can die; kernel before host dedup.
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
    // Schema-level contract: every finding is a work order with a remedy.
    // replace requires a non-empty replacement brief; delete/extract/inline/refactor
    // use replacement "".
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

fn normalize_repo_path(p) {
    // Accept absolute or relative paths; return repo-relative when possible.
    if type_of(p) != "string" { return ""; }
    let s = p;
    // Strip common absolute prefixes (agents often return abs paths).
    // Match trailing repo-relative segments by searching for known roots.
    let markers = [
        "/tests/",
        "/sudo/",
        "/bookmarklet/",
        "/src/infinite_craft_cli/",
        "/.grok/",
    ];
    for m in markers {
        // find last occurrence via scan
        let idx = -1;
        let i = 0;
        while i + m.len() <= s.len() {
            if s.sub_string(i, m.len()) == m {
                idx = i;
            }
            i += 1;
        }
        if idx >= 0 {
            // drop leading slash from marker: "/tests/" → "tests/..."
            return s.sub_string(idx + 1, s.len() - (idx + 1));
        }
    }
    // Already relative?
    s
}

fn path_in_scope(p) {
    // Script-side invariant: only touch this project's known surfaces.
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

fn merge_act_output(out) {
    // Parse act_schema agent output into a plain map.
    let local_changed = [];
    let local_deleted = [];
    let local_added = [];
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
    }
    #{
        ok: ok,
        summary: summary,
        notes: notes,
        changed: local_changed,
        deleted: local_deleted,
        added: local_added,
    }
}

// Note: Rhai fn bodies do not close over outer `let` maps — pass schemas in.
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
    d += "6. Then SIMPLIFY: one confirm helper, one parameterized lane worker, empty-string target, ";
    d += "inline pure re-exports of craft.*, direct chrome_snapshot calls.\n";
    d += "7. Do NOT fully split cli.py into modules in this workflow unless an action explicitly says so.\n";
    d += "8. Do NOT commit or push unless the plan explicitly includes a commit action (default: no commit).\n";
    d += "9. KEEP high-value behavior: dual-queue UX harnesses, confirm FSM harnesses, target set/show/clear, ";
    d += "ratelimit chrome math, noninteractive CLI, parity lockstep, kernel tests.\n";
    d
}

fn default_verify_cmd() {
    // ONLY package globs — never named //pkg:target that a cull can delete.
    // History: hard-coded //tests:test_trainer_parity, //tests:test_extension_loader,
    // and //bookmarklet:kernel_smoke_test each blocked the loop after intentional deletes.
    // Globs expand to whatever is still declared in those packages.
    let c = "bazel test ";
    c += "//sudo/... ";
    c += "//tests/... ";
    c += "//tests/parity/... ";
    c += "//bookmarklet/... ";
    c += "&& bazel build //bookmarklet:trainer_js";
    c
}

// --- args --------------------------------------------------------------------

let max_rounds = 3;
// 0 = no cap (default). Optional debug: pass max_actions_per_round > 0 to truncate plans.
let max_actions = 0;
let dry_run = false;
let approve_each = false;
let focus = "all";
let base_ref = "HEAD";
let verify_cmd = default_verify_cmd();
let act_mode = "sequential";
let use_grok = false;

if args != () {
    // max_rounds: only floor at 1 — caller-chosen cap is the runaway limit for rounds.
    if args.max_rounds != () { max_rounds = at_least_int(args.max_rounds, 1, 3); }
    // Optional debug-only action cap. Default is unlimited (0).
    if args.max_actions_per_round != () {
        max_actions = at_least_int(args.max_actions_per_round, 0, 0);
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
    + " focus=" + focus
    + " dry_run=" + dry_run.to_string()
    + " act_mode=" + act_mode
    + " use_grok=" + use_grok.to_string());

// --- loop state --------------------------------------------------------------

let round = 0;
let empty_streak = 0;
let last_fp = "";
let history = [];
let all_deleted_tests = [];
let all_added_tests = [];
let all_changed = [];

while round < max_rounds {
    round += 1;
    let round_label = "round-" + round.to_string();
    log("--- " + round_label + " ---");

    // ── Discover ────────────────────────────────────────────────────────────
    phase("Discover");

    let doctrine = doctrine_text();
    let scope_blob = "";
    scope_blob += "In-scope paths only (repo-RELATIVE, never absolute):\n";
    scope_blob += "  sudo/craft.sudo, src/infinite_craft_cli/**, bookmarklet/**, tests/**\n";
    scope_blob += "Always set finding.paths to relative paths like \"bookmarklet/trainer.src.mjs\" ";
    scope_blob += "or \"src/infinite_craft_cli/cli.py\" — NOT /Users/... absolute paths.\n";
    scope_blob += "Focus filter for this run: " + focus + "\n";
    scope_blob += "You MUST use tools (grep, read_file, list_dir, git). ";
    scope_blob += "Empty findings only after you actually inspected the tree.\n";
    scope_blob += "For focus=seams: report concrete drop_seam items (aliases, pure re-export wrappers, ";
    scope_blob += "getattr soup, remaining() dual API, deprecated currentCommand) with file paths.\n";

    let scan_jobs = [];

    // tests scanner
    if focus == "all" || focus == "tests" {
        let p = "";
        p += doctrine + "\n" + scope_blob;
        p += "DIMENSION: cull_test (tests that need change now)\n";
        p += "Audit tests/ for low-value or implementation-asserting tests. Prioritize:\n";
        p += "- tests that read source and assert identifiers / index order\n";
        p += "- tests that poke cli._command_queue, _chrome_*, private workers as assertions\n";
        p += "- tests that only exist to pin thin wrappers or getattr seams\n";
        p += "- tests duplicated by test_interactive_ux harnesses or tests/parity\n";
        p += "Read at least: tests/test_interactive.py, tests/test_interactive_ux.py (sample via grep), ";
        p += "tests/test_formatting.py, tests/test_bulk.py, tests/test_cli.py, tests/parity/.\n";
        p += "FINDINGS ARE WORK ORDERS ONLY:\n";
        p += "- Emit a finding only when you want the implementer to change the suite now.\n";
        p += "- Set remedy=delete when coverage already exists elsewhere (name that coverage in detail).\n";
        p += "- Set remedy=replace when the bad test still guards behavior that would be untested after delete; ";
        p += "put a concrete replacement brief in replacement (what behavioral test to add, where, what it asserts). ";
        p += "Implementers must ADD that test, not only delete.\n";
        p += "- replacement=\"\" when remedy is delete.\n";
        p += "- If a test is fine and should stay, emit NOTHING about it (not even a 'keep' finding). Use notes only for ";
        p += "global remarks. Empty findings means the suite is fine for this dimension.\n";
        p += "- Do not invent inventory rows for completeness.\n";
        p += "Do NOT propose deleting high-value dual-queue/confirm harnesses without a replace that preserves that coverage.\n";
        p += "Return dimension=\"tests\".\n";
        scan_jobs.push(#{
            prompt: p,
            label: round_label + ":scan-tests",
            capability_mode: "read-only",
            output_schema: findings_schema,
        });
    }

    // seams scanner
    if focus == "all" || focus == "seams" {
        let p = "";
        p += doctrine + "\n" + scope_blob;
        p += "DIMENSION: drop_seam\n";
        p += "Find production seams that exist mainly for tests or as dead aliases:\n";
        p += "- JS currentCommand deprecated alias, ensureQueueWorker alias\n";
        p += "- Python pure re-export wrappers of craft.*\n";
        p += "- getattr soup / dual APIs only for tests\n";
        p += "Use grep/read_file on cli.py, ratelimit.py, trainer.src.mjs and tests that reference them.\n";
        p += "WORK ORDERS ONLY: emit findings you want implemented now.\n";
        p += "remedy=inline or delete for pure removals; remedy=replace if tests must be rewritten to the new surface ";
        p += "(describe the new test approach in replacement). If a seam must stay for harnesses, emit nothing for it.\n";
        p += "Return dimension=\"seams\".\n";
        scan_jobs.push(#{
            prompt: p,
            label: round_label + ":scan-seams",
            capability_mode: "read-only",
            output_schema: findings_schema,
        });
    }

    // kernel candidates
    if focus == "all" || focus == "kernel" {
        let p = "";
        p += doctrine + "\n" + scope_blob;
        p += "DIMENSION: kernel_extract\n";
        p += "Find pure logic still duplicated or host-only that belongs in sudo/craft.sudo.\n";
        p += "Read craft.sudo exports AND matching host call sites.\n";
        p += "WORK ORDERS ONLY. remedy=extract for new kernel APIs + host wiring. ";
        p += "remedy=replace if tests that pinned the old host path must become kernel/parity tests (spell that out in replacement).\n";
        p += "If nothing should change now, findings=[]. Return dimension=\"kernel\".\n";
        scan_jobs.push(#{
            prompt: p,
            label: round_label + ":scan-kernel",
            capability_mode: "read-only",
            output_schema: findings_schema,
        });
    }

    // host duplication
    if focus == "all" || focus == "hosts" || focus == "simplify" {
        let p = "";
        p += doctrine + "\n" + scope_blob;
        p += "DIMENSION: host_dedup and simplify\n";
        p += "Compare Python cli.py vs JS trainer.src.mjs for cloned control flow worth collapsing.\n";
        p += "WORK ORDERS ONLY. remedy=refactor/inline/extract as appropriate; replace if tests must follow.\n";
        p += "If nothing should change now, findings=[]. Do not propose full file splits of cli.py.\n";
        p += "Return dimension=\"hosts\".\n";
        scan_jobs.push(#{
            prompt: p,
            label: round_label + ":scan-hosts",
            capability_mode: "read-only",
            output_schema: findings_schema,
        });
    }

    if scan_jobs.len() == 0 {
        complete(#{
            summary: "No scanners for focus=" + focus,
            rounds: history,
        });
    }

    let b = budget();
    if b.remaining < scan_jobs.len() + 4 {
        log("budget low before scan; stopping");
        break;
    }

    let scan_results = parallel(scan_jobs);

    let pooled = [];
    let si = 0;
    let raw_count = 0;
    let dropped_kind = 0;
    let dropped_path = 0;
    let dropped_bad_remedy = 0;
    for r in scan_results {
        if r != () && r.success && r.output != () && r.output.findings != () {
            for f in r.output.findings {
                if f == () { continue; }
                raw_count += 1;
                let k = as_str(f.kind, "");
                if !focus_allows(focus, k) {
                    dropped_kind += 1;
                    continue;
                }
                if !finding_has_valid_remedy(f) {
                    dropped_bad_remedy += 1;
                    log("drop finding missing valid remedy/replacement: "
                        + as_str(f.id, "?") + " remedy=" + as_str(f.remedy, "?"));
                    continue;
                }
                let paths = filter_paths(f.paths);
                // require at least one in-scope path, or allow cull_test with tests/ implied by title
                if paths.len() == 0 && k != "cull_test" {
                    dropped_path += 1;
                    log("drop finding (paths out of scope): " + as_str(f.title, "?")
                        + " raw_paths=" + json_encode(f.paths));
                    continue;
                }
                if paths.len() == 0 && k == "cull_test" {
                    // force tests/ prefix if agent omitted paths
                    paths = ["tests/"];
                }
                f.paths = paths;
                pooled.push(f);
            }
        } else {
            log("scanner slot " + si.to_string() + " failed or empty");
        }
        si += 1;
    }

    log("pooled findings: " + pooled.len().to_string()
        + " (raw=" + raw_count.to_string()
        + " dropped_kind=" + dropped_kind.to_string()
        + " dropped_path=" + dropped_path.to_string()
        + " dropped_bad_remedy=" + dropped_bad_remedy.to_string() + ")");

    // ── Plan ────────────────────────────────────────────────────────────────
    phase("Plan");

    let findings_json = json_encode(pooled);
    let plan_prompt = "";
    plan_prompt += doctrine + "\n";
    plan_prompt += "You are the planner for round " + round.to_string() + " of cleanup-loop.\n";
    plan_prompt += "Findings are work orders with remedy delete|replace|extract|inline|refactor.\n";
    plan_prompt += "Every finding must become an action (or merge). No action-count cap unless debug-capped below.\n";
    plan_prompt += "For remedy=replace: the action.spec MUST include (1) what to remove and (2) the full replacement ";
    plan_prompt += "test/code to add (from finding.replacement). Implementers must not delete-only on replace.\n";
    plan_prompt += "For remedy=delete: name existing coverage that remains (or why coverage is unnecessary).\n";
    plan_prompt += "Carry finding.remedy onto each action.remedy.\n";
    if max_actions > 0 {
        plan_prompt += "DEBUG CAP: produce at most " + max_actions.to_string() + " actions this round.\n";
    }
    plan_prompt += "ORDER by kind rank: cull_test, drop_seam, kernel_extract, host_dedup, simplify; high conf first.\n";
    plan_prompt += "Set done=true only if findings empty OR nothing safe to do.\n";
    plan_prompt += "Each action.spec: imperative, exact files/symbols, done criteria. Prefer one file per action.\n";
    plan_prompt += "Do not invent paths outside findings. Merge only same-file same-kind edits.\n";
    plan_prompt += "Skip already-applied work. Rationale: findings_count, actions_count, skips with reasons.\n";
    plan_prompt += "FINDINGS JSON:\n";
    plan_prompt += findings_json + "\n";
    plan_prompt += "If findings empty: done=true, actions=[].\n";

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
                let k = as_str(a.kind, "");
                if !focus_allows(focus, k) { continue; }
                let paths = filter_paths(a.paths);
                if paths.len() == 0 && k == "cull_test" { paths = ["tests/"]; }
                if paths.len() == 0 { continue; }
                a.paths = paths;
                actions.push(a);
            }
        }
    } else {
        log("planner failed; treating round as empty");
        plan_done = true;
        plan_rationale = "planner failed";
    }

    // Stable-sort by kind rank. Optional debug cap only if max_actions > 0.
    let ordered = [];
    let ranks = [0, 1, 2, 3, 4];
    for rk in ranks {
        for a in actions {
            if kind_rank(as_str(a.kind, "")) == rk {
                if max_actions > 0 && ordered.len() >= max_actions {
                    // debug truncate only
                } else {
                    ordered.push(a);
                }
            }
        }
    }
    if max_actions > 0 && actions.len() > max_actions {
        log("DEBUG: truncated plan from " + actions.len().to_string()
            + " to " + max_actions.to_string() + " actions");
    }
    actions = ordered;

    let actions_fp = fingerprint(json_encode(actions));
    log("planned actions: " + actions.len().to_string() + " fp=" + actions_fp);
    if plan_rationale != "" { log("plan: " + plan_rationale); }

    if actions.len() == 0 || plan_done {
        empty_streak += 1;
        history.push(#{
            round: round,
            actions: 0,
            dry: true,
            rationale: plan_rationale,
            findings: pooled.len(),
        });
        if empty_streak >= 2 {
            log("two empty rounds — done");
            break;
        }
        if actions.len() == 0 {
            log("empty plan this round");
            if dry_run {
                let report = "";
                report += "# cleanup-loop dry run (round " + round.to_string() + ")\n\n";
                report += "No actions planned.\n\n";
                report += "Rationale: " + plan_rationale + "\n\n";
                report += "Raw findings count: " + pooled.len().to_string() + "\n\n";
                report += "Findings JSON:\n\n```json\n" + findings_json + "\n```\n";
                let path = write_scratch_file("dry-run-round-" + round.to_string() + ".md", report);
                complete(#{
                    summary: "dry_run: no actions planned",
                    history: history,
                    report: path,
                    actions: [],
                    findings: pooled,
                });
            }
            continue;
        }
    } else {
        empty_streak = 0;
    }

    if last_fp != "" && actions_fp == last_fp {
        log("action fingerprint stalled vs previous round — stop");
        history.push(#{
            round: round,
            actions: actions.len(),
            stalled: true,
        });
        break;
    }
    last_fp = actions_fp;

    // dry_run never implements — one discover+plan cycle then exit.
    if dry_run {
        let report = "";
        report += "# cleanup-loop dry run (round " + round.to_string() + ")\n\n";
        report += plan_rationale + "\n\n";
        report += "## Planned actions (" + actions.len().to_string() + ")\n\n";
        for a in actions {
            report += "- **" + as_str(a.kind, "") + "**: " + as_str(a.title, "") + "\n";
            report += "  - spec: " + as_str(a.spec, "") + "\n";
            report += "  - paths: " + json_encode(a.paths) + "\n";
        }
        report += "\n## Raw findings count: " + pooled.len().to_string() + "\n\n";
        report += "```json\n" + findings_json + "\n```\n";
        let path = write_scratch_file("dry-run-round-" + round.to_string() + ".md", report);
        history.push(#{
            round: round,
            dry_run: true,
            actions: actions.len(),
            findings: pooled.len(),
            report: path,
        });
        complete(#{
            summary: "dry_run: " + actions.len().to_string() + " actions planned",
            history: history,
            report: path,
            actions: actions,
            findings: pooled,
            focus: focus,
            dry_run: true,
        });
    }

    if approve_each {
        let msg = "Round " + round.to_string() + ": implement " + actions.len().to_string()
            + " actions? Resume to continue, or stop the workflow to abort.";
        await_user("user", msg);
        empty_streak = 0;
    }

    // ── Act ─────────────────────────────────────────────────────────────────
    phase("Act");

    // Persist plan so implementers can re-read a stable artifact.
    let plan_path = write_scratch_file(
        "plan-round-" + round.to_string() + ".json",
        json_encode(#{
            round: round,
            focus: focus,
            rationale: plan_rationale,
            actions: actions,
        }),
    );
    log("wrote plan " + plan_path);

    // Budget: sequential = 1 agent per action + 1 retry batch + verify + fix + verify2
    let need = actions.len() + 4;
    if act_mode == "batch" { need = 5; }
    b = budget();
    if b.remaining < need {
        log("budget too low to act+verify (need ~" + need.to_string() + ")");
        break;
    }

    // Shared implementer preamble (dirty-tree tolerant; finish the plan).
    let act_rules = "";
    act_rules += doctrine + "\n";
    act_rules += "You are the IMPLEMENTER for infinite-craft-cli cleanup-loop. Finish the work.\n";
    act_rules += "Workspace: git repo with sudo/craft.sudo, tests/, src/infinite_craft_cli/, bookmarklet/.\n";
    act_rules += "CRITICAL RULES:\n";
    act_rules += "1. USE tools: read_file, grep, search_replace/write, run_terminal_command. Do not claim done without editing.\n";
    act_rules += "2. Apply ONLY the assigned action(s). Do not freestyle extra culls or restores.\n";
    act_rules += "3. Dirty git tree is NORMAL. Prior rounds or partial work may already be unstaged. ";
    act_rules += "Do NOT git restore / git checkout to 'clean up'. Do NOT commit or push.\n";
    act_rules += "4. If the assigned change is already present in the working tree, set ok=true, ";
    act_rules += "notes='already applied', changed_paths=[] (or the file if you touched it).\n";
    act_rules += "5. After test file edits, py_compile the touched files. Do not run full bazel here.\n";
    act_rules += "6. remedy=delete: remove only; coverage must already exist or be unnecessary per the spec.\n";
    act_rules += "7. remedy=replace: you MUST add the replacement test/code described in the spec, then remove the ";
    act_rules += "bad test. Delete-only on a replace action is a failed action (ok=false).\n";
    act_rules += "8. List new tests in added_tests and removed ones in deleted_tests.\n";
    act_rules += "9. You MUST hand-edit. Completing the action is the job.\n";

    let changed_paths = [];
    let deleted_tests = [];
    let added_tests = [];
    let act_notes = "";
    let ok_count = 0;
    let fail_count = 0;
    let failed_actions = [];
    let act_summaries = [];

    if act_mode == "batch" {
        let act_prompt = act_rules;
        act_prompt += "Implement ALL of the following actions IN ORDER as one batch.\n";
        act_prompt += "Plan artifact: " + plan_path + "\n";
        act_prompt += "ACTIONS JSON:\n" + json_encode(actions) + "\n";
        act_prompt += "Return ok=true only if every action is applied or already present.\n";
        let act_r = agent(act_prompt, build_act_opts(round_label + ":act-batch", use_grok, act_schema));
        let parsed = #{ ok: false, summary: "batch act failed", notes: "", changed: [], deleted: [], added: [] };
        if act_r != () && act_r.success && act_r.output != () {
            parsed = merge_act_output(act_r.output);
        }
        act_notes = parsed.notes;
        act_summaries.push(parsed.summary);
        for p in parsed.changed {
            changed_paths.push(p);
            all_changed.push(p);
        }
        for t in parsed.deleted {
            deleted_tests.push(t);
            all_deleted_tests.push(t);
        }
        for t in parsed.added {
            added_tests.push(t);
            all_added_tests.push(t);
        }
        if parsed.ok {
            ok_count = actions.len();
        } else {
            fail_count = actions.len();
            for a in actions { failed_actions.push(a); }
            log("batch act failed: " + parsed.summary);
        }
    } else {
        // sequential: one agent per action
        let ai = 0;
        for a in actions {
            ai += 1;
            let ajson = json_encode(a);
            let act_prompt = act_rules;
            act_prompt += "SINGLE ACTION " + ai.to_string() + " of " + actions.len().to_string() + ".\n";
            act_prompt += "Implement EXACTLY this action and nothing else:\n";
            act_prompt += ajson + "\n";
            act_prompt += "Steps: (1) git status --short (context only, do not restore)\n";
            act_prompt += "(2) read the target file(s)\n";
            act_prompt += "(3) apply the edit with search_replace/write\n";
            act_prompt += "(4) py_compile if Python\n";
            act_prompt += "(5) return structured result.\n";
            act_prompt += "If the named test/symbol is already absent, ok=true notes=already applied.\n";

            let act_r = agent(
                act_prompt,
                build_act_opts(round_label + ":act-" + ai.to_string(), use_grok, act_schema),
            );
            let parsed = #{ ok: false, summary: "act failed", notes: "", changed: [], deleted: [], added: [] };
            if act_r != () && act_r.success && act_r.output != () {
                parsed = merge_act_output(act_r.output);
            } else {
                parsed.notes = "agent failure or schema miss";
            }
            act_notes = act_notes + " | [" + ai.to_string() + "] " + parsed.notes;
            act_summaries.push(parsed.summary);
            for p in parsed.changed {
                changed_paths.push(p);
                all_changed.push(p);
            }
            for t in parsed.deleted {
                deleted_tests.push(t);
                all_deleted_tests.push(t);
            }
            for t in parsed.added {
                added_tests.push(t);
                all_added_tests.push(t);
            }
            if parsed.ok {
                ok_count += 1;
                log("act " + ai.to_string() + " ok: " + parsed.summary);
            } else {
                fail_count += 1;
                failed_actions.push(a);
                log("act " + ai.to_string() + " FAIL: " + parsed.summary);
            }
        }
    }

    // One retry pass for failed actions (always session implementer — not grok).
    if failed_actions.len() > 0 {
        log("retry " + failed_actions.len().to_string() + " failed action(s) with session implementer");
        let retry_prompt = act_rules;
        retry_prompt += "RETRY PASS. Previous implementer failed these actions. YOU must apply them now.\n";
        retry_prompt += "Do not refuse. Do not restore other files. Dirty tree is fine.\n";
        retry_prompt += "FAILED ACTIONS JSON:\n" + json_encode(failed_actions) + "\n";
        retry_prompt += "Apply all of them. ok=true if all applied or already present.\n";
        let retry_r = agent(
            retry_prompt,
            build_act_opts(round_label + ":act-retry", false, act_schema),
        );
        if retry_r != () && retry_r.success && retry_r.output != () {
            let parsed = merge_act_output(retry_r.output);
            act_notes = act_notes + " | retry: " + parsed.notes;
            for p in parsed.changed {
                changed_paths.push(p);
                all_changed.push(p);
            }
            for t in parsed.deleted {
                deleted_tests.push(t);
                all_deleted_tests.push(t);
            }
            for t in parsed.added {
                added_tests.push(t);
                all_added_tests.push(t);
            }
            if parsed.ok {
                ok_count += failed_actions.len();
                fail_count = 0;
                failed_actions = [];
                log("retry ok: " + parsed.summary);
            } else {
                log("retry still incomplete: " + parsed.summary);
            }
        }
    }

    let act_ok = fail_count == 0 && ok_count > 0;
    // Partial progress still counts for verify
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
            notes: act_notes,
            failed: failed_actions.len(),
        });
        empty_streak += 1;
        if empty_streak >= 2 { break; }
        // Last-ditch: one session agent gets the whole plan again
        log("no progress — last-ditch full-plan implement");
        let last_prompt = act_rules;
        last_prompt += "LAST DITCH. Implement the entire plan. Previous agents made zero progress.\n";
        last_prompt += "ACTIONS:\n" + json_encode(actions) + "\n";
        let last_r = agent(last_prompt, build_act_opts(round_label + ":act-last", false, act_schema));
        if last_r != () && last_r.success && last_r.output != () {
            let parsed = merge_act_output(last_r.output);
            if parsed.ok || parsed.changed.len() > 0 {
                for p in parsed.changed {
                    changed_paths.push(p);
                    all_changed.push(p);
                }
                for t in parsed.deleted {
                    deleted_tests.push(t);
                    all_deleted_tests.push(t);
                }
                act_notes = act_notes + " | last: " + parsed.notes;
                any_progress = true;
                act_ok = parsed.ok;
                act_summary = "last-ditch: " + parsed.summary;
            }
        }
        if !any_progress {
            await_user(
                "no_progress",
                "cleanup-loop act made no progress on " + actions.len().to_string()
                    + " actions. Inspect tree / plan, then resume to try next round or stop.",
            );
            continue;
        }
    }

    // ── Verify (always if we changed anything) ──────────────────────────────
    phase("Verify");

    let v_prompt = "";
    v_prompt += "Run this verification command from the repo root and report results.\n";
    v_prompt += "You MUST use the shell/terminal tool and wait for exit code.\n";
    v_prompt += "Command:\n" + verify_cmd + "\n";
    v_prompt += "Set passed=true only if exit code is 0. List failed bazel targets if any.\n";
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
        fix_prompt += "Verification FAILED after cleanup edits. Fix with minimal diffs only.\n";
        fix_prompt += "Verify summary: " + v_summary + "\n";
        fix_prompt += "Actions this round: " + json_encode(actions) + "\n";
        fix_prompt += "Changed paths: " + json_encode(changed_paths) + "\n";
        fix_prompt += "Re-run until green:\n" + verify_cmd + "\n";
        fix_prompt += "ok=true only if verify exits 0. Put verify status in notes.\n";
        fix_prompt += "Do NOT restore culled low-value tests (source-assert, private-poke). ";
        fix_prompt += "If a bazel target is missing because a test file was deleted, prefer adapting ";
        fix_prompt += "mocks/call sites or dropping that target — never resurrect implementation-assert tests.\n";
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

        let v_opts2 = #{
            label: round_label + ":verify2",
            capability_mode: "all",
            output_schema: verify_schema,
        };
        let v2 = agent(v_prompt, v_opts2);
        passed = false;
        if v2 != () && v2.success && v2.output != () {
            passed = as_bool(v2.output.passed, false);
            v_summary = as_str(v2.output.summary, "");
        }

        if !passed {
            log("still red after fix — pausing for user");
            history.push(#{
                round: round,
                act_ok: act_ok,
                verify: false,
                summary: act_summary,
                verify_summary: v_summary,
                fix_attempted: true,
                fix_ok: fix_ok,
                notes: act_notes,
                changed_paths: changed_paths,
            });
            await_user(
                "verification",
                "cleanup-loop verify still failing after one fix attempt. "
                    + "Inspect the tree, then resume to continue to the next round, or stop the workflow.",
            );
            empty_streak = 0;
            continue;
        }
    }

    // ── Round bookkeeping ───────────────────────────────────────────────────
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
    });
    log("round " + round.to_string() + " green: " + act_summary);
    if fail_count > 0 {
        // partial success — still advance, but note remaining work next round
        empty_streak = 0;
    }
}

// --- final report ------------------------------------------------------------

let report = "";
report += "# cleanup-loop report\n\n";
report += "- focus: " + focus + "\n";
report += "- rounds completed: " + history.len().to_string() + "\n";
report += "- dry_run: " + dry_run.to_string() + "\n\n";
report += "## History\n\n";
report += "```json\n" + json_encode(history) + "\n```\n\n";
report += "## Deleted tests (claimed)\n\n";
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
    // unique-ish dump
    for p in all_changed {
        report += "- `" + p + "`\n";
    }
}
report += "\n## Doctrine reminder\n\n";
report += doctrine_text();

let report_path = write_scratch_file("cleanup-loop-report.md", report);
log("wrote " + report_path);

complete(#{
    summary: "cleanup-loop finished after " + history.len().to_string() + " round record(s)",
    history: history,
    deleted_tests: all_deleted_tests,
    added_tests: all_added_tests,
    changed_paths: all_changed,
    report: report_path,
    focus: focus,
    dry_run: dry_run,
});
