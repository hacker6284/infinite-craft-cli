/* Built artifact — do not edit. Single source of truth: trainer.src.mjs
 * (UI/effects) + ../sudo/craft.sudo (kernel, transpiled via sudoc). */
(() => {
  // _sudo/_sudo_rt.mjs
  var I64_MIN = -(2n ** 63n);
  var I64_MAX = 2n ** 63n - 1n;
  var SudoTrap = class extends Error {
    /** A defined runtime fault (spec §8). Kind is one of the closed set. */
    constructor(kind, detail = "") {
      super(detail ? `${kind}: ${detail}` : kind);
      this.name = "SudoTrap";
      this.kind = kind;
      this.detail = detail;
    }
  };
  function chk(x) {
    if (x < I64_MIN || x > I64_MAX) {
      throw new SudoTrap("Overflow");
    }
    return x;
  }
  function mod_i64(a, b) {
    if (b === 0n) {
      throw new SudoTrap("DivByZero");
    }
    let r = a % b;
    if (r !== 0n && a < 0n !== b < 0n) {
      r += b;
    }
    return r;
  }
  function neg(x) {
    return chk(-x);
  }
  var Some = class {
    constructor(value) {
      this.value = value;
    }
  };
  var NoneOpt = class {
    constructor() {
    }
  };
  var NONE = new NoneOpt();
  var Ok = class {
    constructor(value) {
      this.value = value;
    }
  };
  var Err = class {
    constructor(error) {
      this.error = error;
    }
  };
  function is_some(o) {
    return o instanceof Some;
  }
  function is_ok(r) {
    return r instanceof Ok;
  }
  function is_none(o) {
    return o instanceof NoneOpt;
  }
  function unwrap(o) {
    if (o instanceof Some) {
      return o.value;
    }
    if (o instanceof Ok) {
      return o.value;
    }
    throw new SudoTrap("UnwrapFailed");
  }
  function get_or(o, default_) {
    if (o instanceof Some) {
      return o.value;
    }
    if (o instanceof Ok) {
      return o.value;
    }
    return default_;
  }
  function dup(v) {
    if (Array.isArray(v)) {
      return v.map(dup);
    }
    if (v instanceof SudoMap) {
      return v._dup();
    }
    if (v instanceof SudoSet) {
      return v._dup();
    }
    if (v instanceof Some) {
      return new Some(dup(v.value));
    }
    if (v instanceof Ok) {
      return new Ok(dup(v.value));
    }
    if (v instanceof Err) {
      return new Err(dup(v.error));
    }
    if (v && typeof v === "object" && v.constructor && v.constructor._sudoKind) {
      const cls = v.constructor;
      const fields = cls._sudoFields || [];
      return new cls(...fields.map((f) => dup(v[f])));
    }
    return v;
  }
  function eq(a, b) {
    if (typeof a === "number" || typeof b === "number") {
      return typeof a === "number" && typeof b === "number" && a === b;
    }
    if (typeof a === "boolean" || typeof b === "boolean") {
      return a === b;
    }
    if (typeof a === "bigint" && typeof b === "bigint") {
      return a === b;
    }
    if (Array.isArray(a) && Array.isArray(b)) {
      if (a.length !== b.length) {
        return false;
      }
      for (let i = 0; i < a.length; i++) {
        if (!eq(a[i], b[i])) {
          return false;
        }
      }
      return true;
    }
    if (a instanceof SudoMap && b instanceof SudoMap) {
      if (a.size !== b.size) {
        return false;
      }
      for (const [k, v] of a.pairs()) {
        const other = b.get_opt(k);
        if (other instanceof NoneOpt || !eq(v, other.value)) {
          return false;
        }
      }
      return true;
    }
    if (a instanceof SudoSet && b instanceof SudoSet) {
      if (a.size !== b.size) {
        return false;
      }
      for (const x of a.items_list()) {
        if (!b.has(x)) {
          return false;
        }
      }
      return true;
    }
    if (a instanceof NoneOpt && b instanceof NoneOpt) {
      return true;
    }
    if (a instanceof Some && b instanceof Some) {
      return eq(a.value, b.value);
    }
    if (a instanceof Ok && b instanceof Ok) {
      return eq(a.value, b.value);
    }
    if (a instanceof Err && b instanceof Err) {
      return eq(a.error, b.error);
    }
    if (a && b && typeof a === "object" && typeof b === "object" && a.constructor && a.constructor._sudoKind && b.constructor && b.constructor._sudoKind) {
      if (a.constructor !== b.constructor) {
        return false;
      }
      const fields = a.constructor._sudoFields || [];
      for (const f of fields) {
        if (!eq(a[f], b[f])) {
          return false;
        }
      }
      return true;
    }
    return false;
  }
  function key_form(v) {
    return JSON.stringify(key_form_raw(v));
  }
  function key_form_raw(v) {
    if (typeof v === "bigint") {
      return ["i", v.toString()];
    }
    if (typeof v === "boolean") {
      return ["b", v];
    }
    if (typeof v === "number") {
      if (Number.isNaN(v)) {
        return ["f", "NaN"];
      }
      if (!Number.isFinite(v)) {
        return ["f", v > 0 ? "Inf" : "-Inf"];
      }
      if (Object.is(v, -0)) {
        return ["f", "-0"];
      }
      return ["f", String(v)];
    }
    if (Array.isArray(v)) {
      return ["a", v.map(key_form_raw)];
    }
    if (v instanceof Some) {
      return ["Some", key_form_raw(v.value)];
    }
    if (v instanceof NoneOpt) {
      return ["None"];
    }
    if (v instanceof Ok) {
      return ["Ok", key_form_raw(v.value)];
    }
    if (v instanceof Err) {
      return ["Err", key_form_raw(v.error)];
    }
    if (v && typeof v === "object" && v.constructor && v.constructor._sudoKind) {
      const fields = v.constructor._sudoFields || [];
      return [v.constructor.name, ...fields.map((f) => key_form_raw(v[f]))];
    }
    return ["?", String(v)];
  }
  function idx(a, i) {
    const n = BigInt(a.length);
    if (i < 0n || i >= n) {
      throw new SudoTrap("OutOfBounds", `index ${i} of length ${a.length}`);
    }
    return Number(i);
  }
  function at(a, i) {
    return a[idx(a, i)];
  }
  function put(a, i, v) {
    a[idx(a, i)] = v;
  }
  function pop(a) {
    if (a.length === 0) {
      throw new SudoTrap("OutOfBounds", "pop from empty list");
    }
    return a.pop();
  }
  function swap(a, i, j) {
    const n = BigInt(a.length);
    if (i < 0n || i >= n || j < 0n || j >= n) {
      throw new SudoTrap("OutOfBounds", `swap ${i},${j} of length ${a.length}`);
    }
    const ii = Number(i);
    const jj = Number(j);
    const tmp = a[ii];
    a[ii] = a[jj];
    a[jj] = tmp;
  }
  function filled(n, v) {
    if (n < 0n) {
      throw new SudoTrap("InvalidArg", `filled(${n})`);
    }
    const count = Number(n);
    const out = new Array(count);
    for (let i = 0; i < count; i++) {
      out[i] = dup(v);
    }
    return out;
  }
  function txt(s) {
    const out = [];
    for (const ch of s) {
      out.push(BigInt(ch.codePointAt(0)));
    }
    return out;
  }
  var SudoMap = class _SudoMap {
    /**
     * Insertion-ordered (Map-backed) — order is unspecified by the language.
     * Keys are stored by structural key_form so Lists and records can be keys;
     * original key values are retained for iteration.
     */
    constructor() {
      this._d = /* @__PURE__ */ new Map();
    }
    get size() {
      return this._d.size;
    }
    has(k) {
      return this._d.has(key_form(k));
    }
    get(k) {
      const kf = key_form(k);
      if (!this._d.has(kf)) {
        throw new SudoTrap("KeyMissing");
      }
      return this._d.get(kf)[1];
    }
    set(k, v) {
      this._d.set(key_form(k), [dup(k), v]);
    }
    get_opt(k) {
      const kf = key_form(k);
      if (this._d.has(kf)) {
        return new Some(this._d.get(kf)[1]);
      }
      return NONE;
    }
    delete(k) {
      const kf = key_form(k);
      if (this._d.has(kf)) {
        this._d.delete(kf);
        return true;
      }
      return false;
    }
    keys_list() {
      const out = [];
      for (const [k] of this._d.values()) {
        out.push(dup(k));
      }
      return out;
    }
    values_list() {
      const out = [];
      for (const [, v] of this._d.values()) {
        out.push(v);
      }
      return out;
    }
    pairs() {
      const out = [];
      for (const [k, v] of this._d.values()) {
        out.push([k, v]);
      }
      return out;
    }
    _dup() {
      const m = new _SudoMap();
      for (const [k, v] of this._d.values()) {
        m._d.set(key_form(k), [dup(k), dup(v)]);
      }
      return m;
    }
  };
  var SudoSet = class _SudoSet {
    constructor() {
      this._d = /* @__PURE__ */ new Map();
    }
    get size() {
      return this._d.size;
    }
    has(v) {
      return this._d.has(key_form(v));
    }
    add(v) {
      const kf = key_form(v);
      if (this._d.has(kf)) {
        return false;
      }
      this._d.set(kf, dup(v));
      return true;
    }
    remove(v) {
      const kf = key_form(v);
      if (this._d.has(kf)) {
        this._d.delete(kf);
        return true;
      }
      return false;
    }
    items_list() {
      const out = [];
      for (const v of this._d.values()) {
        out.push(dup(v));
      }
      return out;
    }
    _dup() {
      const s = new _SudoSet();
      for (const [k, v] of this._d.entries()) {
        s._d.set(k, dup(v));
      }
      return s;
    }
  };
  function sudo_assert(cond, line) {
    if (!cond) {
      throw new SudoTrap("AssertFailed", `line ${line}`);
    }
  }
  function canon(v) {
    if (typeof v === "boolean") {
      return v ? "true" : "false";
    }
    if (typeof v === "bigint") {
      return v.toString();
    }
    if (typeof v === "number") {
      let s;
      if (Number.isNaN(v)) {
        s = "NaN";
      } else if (!Number.isFinite(v)) {
        s = v > 0 ? "Inf" : "-Inf";
      } else if (Object.is(v, -0)) {
        s = "-0.0";
      } else {
        s = String(v);
        if (/^-?\d+$/.test(s)) {
          s = s + ".0";
        }
      }
      return `{"f": "${s}"}`;
    }
    if (Array.isArray(v)) {
      return "[" + v.map(canon).join(", ") + "]";
    }
    if (v instanceof SudoMap) {
      const pairs = v.pairs().map(([k, x]) => `[${canon(k)}, ${canon(x)}]`).join(", ");
      return `{"m": [${pairs}]}`;
    }
    if (v instanceof SudoSet) {
      return `{"s": [${v.items_list().map(canon).join(", ")}]}`;
    }
    if (v instanceof Some) {
      return `{"e": "Option.Some", "v": [${canon(v.value)}]}`;
    }
    if (v instanceof NoneOpt) {
      return `{"e": "Option.None"}`;
    }
    if (v instanceof Ok) {
      return `{"e": "Result.Ok", "v": [${canon(v.value)}]}`;
    }
    if (v instanceof Err) {
      return `{"e": "Result.Err", "v": [${canon(v.error)}]}`;
    }
    if (v && typeof v === "object" && v.constructor && v.constructor._sudoKind) {
      const [kind, name] = v.constructor._sudoKind;
      const fields = v.constructor._sudoFields || [];
      const vals = fields.map((f) => canon(v[f])).join(", ");
      if (vals) {
        return `{"${kind}": "${name}", "v": [${vals}]}`;
      }
      return `{"${kind}": "${name}"}`;
    }
    return String(v);
  }
  function sudo_assert_eq(l, r, line) {
    if (!eq(l, r)) {
      throw new SudoTrap("AssertFailed", `line ${line}: ${canon(l)} != ${canon(r)}`);
    }
  }
  var MAX_SAFE_BI = BigInt(Number.MAX_SAFE_INTEGER);
  var MIN_SAFE_BI = -MAX_SAFE_BI;
  function int_out(x) {
    if (x < MIN_SAFE_BI || x > MAX_SAFE_BI) {
      throw new RangeError("int exceeds Number.MAX_SAFE_INTEGER");
    }
    return Number(x);
  }
  function host_bool(x) {
    if (typeof x !== "boolean") {
      throw new TypeError("expected a boolean");
    }
    return x;
  }
  function host_text(s) {
    if (typeof s !== "string") {
      throw new TypeError("expected a string");
    }
    const out = [];
    for (let i = 0; i < s.length; i++) {
      const c = s.codePointAt(i);
      if (c >= 55296 && c <= 57343) {
        const hex = c.toString(16).toUpperCase().padStart(4, "0");
        throw new SudoTrap(
          "InvalidConvert",
          `lone surrogate U+${hex} at index ${i}`
        );
      }
      out.push(BigInt(c));
      if (c > 65535) {
        i++;
      }
    }
    return out;
  }
  function text_str(v) {
    let s = "";
    for (const c of v) {
      s += String.fromCodePoint(Number(c));
    }
    return s;
  }
  function host_list(x, conv) {
    if (typeof x === "string" || typeof x?.[Symbol.iterator] !== "function") {
      throw new TypeError("expected an iterable");
    }
    const out = [];
    for (const v of x) {
      out.push(conv(v));
    }
    return out;
  }
  function host_map(x, kconv, vconv) {
    const m = new SudoMap();
    if (x instanceof Map) {
      for (const [k, v] of x) {
        m.set(kconv(k), vconv(v));
      }
    } else if (x && typeof x === "object") {
      for (const k of Object.keys(x)) {
        m.set(kconv(k), vconv(x[k]));
      }
    } else {
      throw new TypeError("expected a Map or plain object");
    }
    return m;
  }
  function host_tuple(x, n, convs) {
    if (typeof x?.[Symbol.iterator] !== "function") {
      throw new TypeError("expected an iterable");
    }
    const arr = Array.from(x);
    if (arr.length !== n) {
      throw new TypeError(`expected a ${n}-tuple, got length ${arr.length}`);
    }
    return arr.map((v, i) => convs[i](v));
  }
  function out_option(o, conv) {
    return o instanceof NoneOpt ? null : conv(o.value);
  }
  function writeback_map(host, fresh, kconv, vconv) {
    if (host instanceof Map) {
      host.clear();
      for (const [k, v] of fresh.pairs()) {
        host.set(kconv(k), vconv(v));
      }
    } else if (host && typeof host === "object") {
      for (const k of Object.keys(host)) {
        delete host[k];
      }
      for (const [k, v] of fresh.pairs()) {
        host[kconv(k)] = vconv(v);
      }
    } else {
      throw new TypeError("expected a Map or plain object");
    }
  }

  // _sudo/_strings_impl.mjs
  function lex_compare(a, b) {
    a = dup(a);
    b = dup(b);
    let n = globalThis.BigInt(a.length) < globalThis.BigInt(b.length) ? globalThis.BigInt(a.length) : globalThis.BigInt(b.length);
    const _sudo_from_i = 0n;
    const _sudo_to_i = chk(n - 1n);
    for (let i = _sudo_from_i; i <= _sudo_to_i; i += 1n) {
      if (at(a, i) < at(b, i)) {
        return neg(1n);
      }
      if (at(b, i) < at(a, i)) {
        return 1n;
      }
    }
    if (globalThis.BigInt(a.length) < globalThis.BigInt(b.length)) {
      return neg(1n);
    }
    if (globalThis.BigInt(b.length) < globalThis.BigInt(a.length)) {
      return 1n;
    }
    return 0n;
  }
  function starts_with(s, prefix) {
    s = dup(s);
    prefix = dup(prefix);
    if (globalThis.BigInt(prefix.length) > globalThis.BigInt(s.length)) {
      return false;
    }
    return match_at(s, prefix, 0n);
  }
  function match_at(hay, needle, start) {
    hay = dup(hay);
    needle = dup(needle);
    const _sudo_from_i = 0n;
    const _sudo_to_i = chk(globalThis.BigInt(needle.length) - 1n);
    for (let i = _sudo_from_i; i <= _sudo_to_i; i += 1n) {
      if (at(hay, chk(start + i)) !== at(needle, i)) {
        return false;
      }
    }
    return true;
  }
  function index_of(hay, needle) {
    hay = dup(hay);
    needle = dup(needle);
    if (globalThis.BigInt(needle.length) === 0n) {
      return new Some(0n);
    }
    if (globalThis.BigInt(needle.length) > globalThis.BigInt(hay.length)) {
      return NONE;
    }
    const _sudo_from_start = 0n;
    const _sudo_to_start = chk(globalThis.BigInt(hay.length) - globalThis.BigInt(needle.length));
    for (let start = _sudo_from_start; start <= _sudo_to_start; start += 1n) {
      if (match_at(hay, needle, start)) {
        return new Some(start);
      }
    }
    return NONE;
  }
  function contains(hay, needle) {
    hay = dup(hay);
    needle = dup(needle);
    return is_some(index_of(hay, needle));
  }
  function to_lower(s) {
    s = dup(s);
    let out = txt("");
    for (const c of s.slice()) {
      if (c >= 65n && c <= 90n) {
        out.push(chk(c + 32n));
      } else {
        out.push(c);
      }
    }
    return dup(out);
  }

  // _sudo/_regex_impl.mjs
  var CompiledPattern = class {
    static _sudoKind = ["r", "CompiledPattern"];
    static _sudoFields = ["states", "passes"];
    constructor(states, passes) {
      this.states = states;
      this.passes = passes;
    }
  };
  var Item = class {
    static _sudoKind = ["r", "Item"];
    static _sudoFields = ["atom", "quant"];
    constructor(atom, quant) {
      this.atom = atom;
      this.quant = quant;
    }
  };
  var BraceQuant = class {
    static _sudoKind = ["r", "BraceQuant"];
    static _sudoFields = ["kind", "m", "n", "end"];
    constructor(kind, m, n, end) {
      this.kind = kind;
      this.m = m;
      this.n = n;
      this.end = end;
    }
  };
  var Branch = class {
    static _sudoKind = ["r", "Branch"];
    static _sudoFields = ["items", "anchored_start", "anchored_end"];
    constructor(items, anchored_start, anchored_end) {
      this.items = items;
      this.anchored_start = anchored_start;
      this.anchored_end = anchored_end;
    }
  };
  var ParseResult = class {
    static _sudoKind = ["r", "ParseResult"];
    static _sudoFields = ["branches"];
    constructor(branches) {
      this.branches = branches;
    }
  };
  var NfaState_CharLit = class {
    static _sudoKind = ["e", "NfaState.CharLit"];
    static _sudoFields = ["ch", "out"];
    constructor(ch, out) {
      this.ch = ch;
      this.out = out;
    }
  };
  var NfaState_CharClass = class {
    static _sudoKind = ["e", "NfaState.CharClass"];
    static _sudoFields = ["negate", "ranges", "out"];
    constructor(negate, ranges, out) {
      this.negate = negate;
      this.ranges = ranges;
      this.out = out;
    }
  };
  var NfaState_Split = class {
    static _sudoKind = ["e", "NfaState.Split"];
    static _sudoFields = ["out1", "out2"];
    constructor(out1, out2) {
      this.out1 = out1;
      this.out2 = out2;
    }
  };
  var NfaState_Match = class {
    static _sudoKind = ["e", "NfaState.Match"];
    static _sudoFields = [];
    constructor() {
    }
  };
  var Atom_Lit = class {
    static _sudoKind = ["e", "Atom.Lit"];
    static _sudoFields = ["ch"];
    constructor(ch) {
      this.ch = ch;
    }
  };
  var Atom_Wildcard = class {
    static _sudoKind = ["e", "Atom.Wildcard"];
    static _sudoFields = [];
    constructor() {
    }
  };
  var Atom_Class = class {
    static _sudoKind = ["e", "Atom.Class"];
    static _sudoFields = ["negate", "ranges"];
    constructor(negate, ranges) {
      this.negate = negate;
      this.ranges = ranges;
    }
  };
  var Quant_Once = class {
    static _sudoKind = ["e", "Quant.Once"];
    static _sudoFields = [];
    constructor() {
    }
  };
  var Quant_Star = class {
    static _sudoKind = ["e", "Quant.Star"];
    static _sudoFields = [];
    constructor() {
    }
  };
  var Quant_Plus = class {
    static _sudoKind = ["e", "Quant.Plus"];
    static _sudoFields = [];
    constructor() {
    }
  };
  var Quant_Opt = class {
    static _sudoKind = ["e", "Quant.Opt"];
    static _sudoFields = [];
    constructor() {
    }
  };
  function fold_ascii(c) {
    if (c >= 65n && c <= 90n) {
      return chk(c + 32n);
    }
    return c;
  }
  function swap_case(c) {
    if (c >= 65n && c <= 90n) {
      return chk(c + 32n);
    }
    if (c >= 97n && c <= 122n) {
      return chk(c - 32n);
    }
    return c;
  }
  function ci_eq(a, b, ignore_case) {
    if (ignore_case) {
      return fold_ascii(a) === fold_ascii(b);
    }
    return a === b;
  }
  function in_ranges(ranges, c) {
    ranges = dup(ranges);
    for (const r of ranges.slice()) {
      let lo;
      let hi;
      [lo, hi] = r;
      if (c >= lo && c <= hi) {
        return true;
      }
    }
    return false;
  }
  function class_matches(negate, ranges, c, ignore_case) {
    ranges = dup(ranges);
    let m = in_ranges(ranges, c);
    if (ignore_case) {
      let alt = swap_case(c);
      if (alt !== c && in_ranges(ranges, alt)) {
        m = true;
      }
    }
    if (negate) {
      return !m;
    }
    return m;
  }
  function parse_class_body(pattern, open_at, caret_negates, validate_ranges, allow_escapes) {
    pattern = dup(pattern);
    let i = chk(open_at + 1n);
    if (i >= globalThis.BigInt(pattern.length)) {
      return new Err(txt("invalid pattern: unclosed character class"));
    }
    let negate = false;
    let is_caret_neg = caret_negates && at(pattern, i) === 94n;
    if (at(pattern, i) === 33n) {
      negate = true;
      i = chk(i + 1n);
    } else if (is_caret_neg) {
      negate = true;
      i = chk(i + 1n);
    }
    let ranges = [];
    if (i < globalThis.BigInt(pattern.length) && at(pattern, i) === 93n) {
      ranges.push([93n, 93n]);
      i = chk(i + 1n);
    }
    while (i < globalThis.BigInt(pattern.length)) {
      if (allow_escapes && at(pattern, i) === 92n) {
        if (chk(i + 1n) >= globalThis.BigInt(pattern.length)) {
          return new Err(txt("invalid pattern: unclosed character class"));
        }
        let e = at(pattern, chk(i + 1n));
        if (e === 100n) {
          ranges.push([48n, 57n]);
          i = chk(i + 2n);
          continue;
        }
        if (e === 119n) {
          ranges.push([65n, 90n]);
          ranges.push([97n, 122n]);
          ranges.push([48n, 57n]);
          ranges.push([95n, 95n]);
          i = chk(i + 2n);
          continue;
        }
        if (e === 115n) {
          ranges.push([32n, 32n]);
          ranges.push([9n, 9n]);
          ranges.push([10n, 10n]);
          ranges.push([13n, 13n]);
          ranges.push([12n, 12n]);
          ranges.push([11n, 11n]);
          i = chk(i + 2n);
          continue;
        }
        if (e === 68n || e === 87n || e === 83n) {
          let msgc = txt("invalid pattern: unsupported escape \\");
          msgc.push(e);
          msgc = dup(msgc.concat(txt(" inside character class")));
          return new Err(dup(msgc));
        }
        if (e === 46n || e === 42n || e === 43n || e === 63n || e === 91n || e === 93n || e === 94n || e === 36n || e === 124n || e === 92n || e === 47n || e === 123n || e === 125n || e === 40n || e === 41n || e === 45n) {
          ranges.push([e, e]);
          i = chk(i + 2n);
          continue;
        }
        let msgu = txt("invalid pattern: unsupported escape \\");
        msgu.push(e);
        return new Err(dup(msgu));
      }
      if (at(pattern, i) === 93n) {
        return new Ok([negate, dup(ranges), chk(i + 1n)]);
      }
      let lo = at(pattern, i);
      i = chk(i + 1n);
      if (i < globalThis.BigInt(pattern.length) && at(pattern, i) === 45n && chk(i + 1n) < globalThis.BigInt(pattern.length) && at(pattern, chk(i + 1n)) !== 93n) {
        let hi = at(pattern, chk(i + 1n));
        i = chk(i + 2n);
        if (validate_ranges && lo > hi) {
          return new Err(txt("invalid pattern: bad character range"));
        }
        ranges.push([lo, hi]);
      } else {
        ranges.push([lo, lo]);
      }
    }
    return new Err(txt("invalid pattern: unclosed character class"));
  }
  function is_digit(c) {
    return c >= 48n && c <= 57n;
  }
  function parse_uint_at(pattern, i) {
    pattern = dup(pattern);
    if (i >= globalThis.BigInt(pattern.length) || !is_digit(at(pattern, i))) {
      return NONE;
    }
    let v = 0n;
    let j = i;
    while (j < globalThis.BigInt(pattern.length) && is_digit(at(pattern, j))) {
      let d = chk(at(pattern, j) - 48n);
      if (v > 100000n) {
        v = 100000n;
      } else {
        v = chk(chk(v * 10n) + d);
      }
      j = chk(j + 1n);
    }
    return new Some([v, j]);
  }
  function try_parse_brace(pattern, i) {
    pattern = dup(pattern);
    if (i >= globalThis.BigInt(pattern.length) || at(pattern, i) !== 123n) {
      return NONE;
    }
    let j = chk(i + 1n);
    let m_opt = parse_uint_at(pattern, j);
    if (is_none(m_opt)) {
      return NONE;
    }
    let m;
    [m, j] = unwrap(m_opt);
    if (j >= globalThis.BigInt(pattern.length)) {
      return NONE;
    }
    if (at(pattern, j) === 125n) {
      return new Some(new BraceQuant(0n, m, m, chk(j + 1n)));
    }
    if (at(pattern, j) !== 44n) {
      return NONE;
    }
    j = chk(j + 1n);
    if (j >= globalThis.BigInt(pattern.length)) {
      return NONE;
    }
    if (at(pattern, j) === 125n) {
      return new Some(new BraceQuant(1n, m, 0n, chk(j + 1n)));
    }
    let n_opt = parse_uint_at(pattern, j);
    if (is_none(n_opt)) {
      return NONE;
    }
    let n;
    [n, j] = unwrap(n_opt);
    if (j >= globalThis.BigInt(pattern.length) || at(pattern, j) !== 125n) {
      return NONE;
    }
    return new Some(new BraceQuant(2n, m, n, chk(j + 1n)));
  }
  function err_bound_cap() {
    return txt("invalid pattern: quantifier bound exceeds 1000");
  }
  function err_bound_order() {
    return txt("invalid pattern: quantifier range min greater than max");
  }
  function err_nothing_to_repeat() {
    return txt("invalid pattern: quantifier with nothing to repeat");
  }
  function append_copies(items, atom, quant, count) {
    const _sudo_from_k = 1n;
    const _sudo_to_k = count;
    for (let k = _sudo_from_k; k <= _sudo_to_k; k += 1n) {
      items.push(new Item(atom, quant));
    }
    return items;
  }
  function expand_brace(items, atom, bq) {
    bq = dup(bq);
    let m = bq.m;
    let n = bq.n;
    if (m > 1000n) {
      return [new Err(err_bound_cap()), items];
    }
    if (bq.kind === 0n) {
      items = append_copies(items, atom, new Quant_Once(), m);
      return [new Ok(true), items];
    }
    if (bq.kind === 1n) {
      items = append_copies(items, atom, new Quant_Once(), m);
      items.push(new Item(atom, new Quant_Star()));
      return [new Ok(true), items];
    }
    if (n > 1000n) {
      return [new Err(err_bound_cap()), items];
    }
    if (m > n) {
      return [new Err(err_bound_order()), items];
    }
    items = append_copies(items, atom, new Quant_Once(), m);
    items = append_copies(items, atom, new Quant_Opt(), chk(n - m));
    return [new Ok(true), items];
  }
  function parse_branch_items(pattern, start_i, end_i, is_glob) {
    pattern = dup(pattern);
    let items = [];
    let i = start_i;
    while (i < end_i) {
      let ch = at(pattern, i);
      if (is_glob) {
        if (ch === 42n) {
          let empty_r = [];
          items.push(new Item(new Atom_Class(true, dup(empty_r)), new Quant_Star()));
          i = chk(i + 1n);
          continue;
        }
        if (ch === 63n) {
          let empty_r2 = [];
          items.push(new Item(new Atom_Class(true, dup(empty_r2)), new Quant_Once()));
          i = chk(i + 1n);
          continue;
        }
        if (ch === 91n) {
          let cr = parse_class_body(pattern, i, false, false, false);
          if (is_ok(cr)) {
            let triple = dup(unwrap(cr));
            let neg2;
            let rng;
            let ni;
            [neg2, rng, ni] = dup(triple);
            items.push(new Item(new Atom_Class(neg2, dup(rng)), new Quant_Once()));
            i = ni;
          } else {
            items.push(new Item(new Atom_Lit(91n), new Quant_Once()));
            i = chk(i + 1n);
          }
          continue;
        }
        items.push(new Item(new Atom_Lit(ch), new Quant_Once()));
        i = chk(i + 1n);
        continue;
      }
      if (ch === 42n || ch === 43n || ch === 63n) {
        return new Err(err_nothing_to_repeat());
      }
      if (ch === 123n) {
        let bq_opt = try_parse_brace(pattern, i);
        if (is_some(bq_opt)) {
          return new Err(err_nothing_to_repeat());
        }
        items.push(new Item(new Atom_Lit(123n), new Quant_Once()));
        i = chk(i + 1n);
        continue;
      }
      let atom = new Atom_Lit(ch);
      let atom_end = chk(i + 1n);
      if (ch === 46n) {
        atom = new Atom_Wildcard();
        atom_end = chk(i + 1n);
      } else if (ch === 91n) {
        let cr2 = parse_class_body(pattern, i, true, true, true);
        {
          const _sudo_sc = cr2;
          if (_sudo_sc instanceof Ok) {
            const triple2 = _sudo_sc.value;
            let neg2;
            let rng2;
            let ni2;
            [neg2, rng2, ni2] = dup(triple2);
            atom = new Atom_Class(neg2, dup(rng2));
            atom_end = ni2;
          } else if (_sudo_sc instanceof Err) {
            const emsg = _sudo_sc.error;
            return new Err(dup(emsg));
          }
        }
      } else if (ch === 92n) {
        let esc_r = parse_escape(pattern, i);
        {
          const _sudo_sc = esc_r;
          if (_sudo_sc instanceof Ok) {
            const pair = _sudo_sc.value;
            let eatom;
            let ni3;
            [eatom, ni3] = pair;
            atom = eatom;
            atom_end = ni3;
          } else if (_sudo_sc instanceof Err) {
            const emsg2 = _sudo_sc.error;
            return new Err(dup(emsg2));
          }
        }
      } else {
        atom = new Atom_Lit(ch);
        atom_end = chk(i + 1n);
      }
      i = atom_end;
      if (i < end_i) {
        let qch = at(pattern, i);
        if (qch === 42n) {
          items.push(new Item(atom, new Quant_Star()));
          i = chk(i + 1n);
        } else if (qch === 43n) {
          items.push(new Item(atom, new Quant_Plus()));
          i = chk(i + 1n);
        } else if (qch === 63n) {
          items.push(new Item(atom, new Quant_Opt()));
          i = chk(i + 1n);
        } else if (qch === 123n) {
          let bq2 = try_parse_brace(pattern, i);
          if (is_some(bq2)) {
            let bq = dup(unwrap(bq2));
            let er;
            [er, items] = expand_brace(items, atom, bq);
            {
              const _sudo_sc = er;
              if (_sudo_sc instanceof Ok) {
                const okv = _sudo_sc.value;
                sudo_assert_eq(okv, okv, 423);
                i = bq.end;
              } else if (_sudo_sc instanceof Err) {
                const em = _sudo_sc.error;
                return new Err(dup(em));
              }
            }
          } else {
            items.push(new Item(atom, new Quant_Once()));
          }
        } else {
          items.push(new Item(atom, new Quant_Once()));
        }
      } else {
        items.push(new Item(atom, new Quant_Once()));
      }
    }
    return new Ok(dup(items));
  }
  function parse_escape(pattern, i) {
    pattern = dup(pattern);
    if (chk(i + 1n) >= globalThis.BigInt(pattern.length)) {
      return new Err(txt("invalid pattern: trailing backslash"));
    }
    let e = at(pattern, chk(i + 1n));
    let ni = chk(i + 2n);
    if (e === 46n || e === 42n || e === 43n || e === 63n || e === 91n || e === 93n || e === 94n || e === 36n || e === 124n || e === 92n || e === 47n || e === 123n || e === 125n || e === 40n || e === 41n) {
      return new Ok([new Atom_Lit(e), ni]);
    }
    if (e === 100n) {
      let r = [[48n, 57n]];
      return new Ok([new Atom_Class(false, dup(r)), ni]);
    }
    if (e === 68n) {
      let r2 = [[48n, 57n]];
      return new Ok([new Atom_Class(true, dup(r2)), ni]);
    }
    if (e === 119n) {
      let r3 = [[65n, 90n], [97n, 122n], [48n, 57n], [95n, 95n]];
      return new Ok([new Atom_Class(false, dup(r3)), ni]);
    }
    if (e === 87n) {
      let r4 = [[65n, 90n], [97n, 122n], [48n, 57n], [95n, 95n]];
      return new Ok([new Atom_Class(true, dup(r4)), ni]);
    }
    if (e === 115n) {
      let r5 = [[32n, 32n], [9n, 9n], [10n, 10n], [13n, 13n], [12n, 12n], [11n, 11n]];
      return new Ok([new Atom_Class(false, dup(r5)), ni]);
    }
    if (e === 83n) {
      let r6 = [[32n, 32n], [9n, 9n], [10n, 10n], [13n, 13n], [12n, 12n], [11n, 11n]];
      return new Ok([new Atom_Class(true, dup(r6)), ni]);
    }
    if (e === 98n || e === 66n) {
      let msgb = txt("invalid pattern: \\");
      msgb.push(e);
      msgb = dup(msgb.concat(txt(" word-boundary assertions are not supported")));
      return new Err(dup(msgb));
    }
    let msgu2 = txt("invalid pattern: unsupported escape \\");
    msgu2.push(e);
    return new Err(dup(msgu2));
  }
  function find_top_level_pipe_spans(pattern) {
    pattern = dup(pattern);
    let spans = [];
    let n = globalThis.BigInt(pattern.length);
    let seg_start = 0n;
    let i = 0n;
    while (i < n) {
      let ch = at(pattern, i);
      if (ch === 92n) {
        if (chk(i + 1n) < n) {
          i = chk(i + 2n);
        } else {
          i = chk(i + 1n);
        }
        continue;
      }
      if (ch === 91n) {
        let cr = parse_class_body(pattern, i, true, false, true);
        if (is_ok(cr)) {
          let triple = dup(unwrap(cr));
          let neg2;
          let rng;
          let ni;
          [neg2, rng, ni] = dup(triple);
          i = ni;
          continue;
        } else {
          i = chk(i + 1n);
          continue;
        }
      }
      if (ch === 124n) {
        spans.push([seg_start, i]);
        seg_start = chk(i + 1n);
        i = chk(i + 1n);
        continue;
      }
      i = chk(i + 1n);
    }
    spans.push([seg_start, n]);
    return dup(spans);
  }
  function is_escaped_at(pattern, idx2, lower_bound) {
    pattern = dup(pattern);
    let count = 0n;
    let j = chk(idx2 - 1n);
    while (j >= lower_bound && at(pattern, j) === 92n) {
      count = chk(count + 1n);
      j = chk(j - 1n);
    }
    return mod_i64(count, 2n) === 1n;
  }
  function parse_pattern(pattern, is_glob) {
    pattern = dup(pattern);
    if (is_glob) {
      let items_r = parse_branch_items(pattern, 0n, globalThis.BigInt(pattern.length), true);
      {
        const _sudo_sc = items_r;
        if (_sudo_sc instanceof Ok) {
          const items = _sudo_sc.value;
          let only_branch = new Branch(dup(items), true, true);
          let only = [dup(only_branch)];
          return new Ok(new ParseResult(dup(only)));
        } else if (_sudo_sc instanceof Err) {
          const msg = _sudo_sc.error;
          return new Err(dup(msg));
        }
      }
    }
    let spans = find_top_level_pipe_spans(pattern);
    let branches = [];
    for (const span of spans.slice()) {
      let s0;
      let e0;
      [s0, e0] = span;
      let anchored_start = false;
      let anchored_end = false;
      let bs = s0;
      let be = e0;
      if (be > bs && at(pattern, bs) === 94n) {
        anchored_start = true;
        bs = chk(bs + 1n);
      }
      if (be > bs && at(pattern, chk(be - 1n)) === 36n && !is_escaped_at(pattern, chk(be - 1n), bs)) {
        anchored_end = true;
        be = chk(be - 1n);
      }
      let items_r = parse_branch_items(pattern, bs, be, false);
      {
        const _sudo_sc = items_r;
        if (_sudo_sc instanceof Ok) {
          const items = _sudo_sc.value;
          branches.push(new Branch(dup(items), anchored_start, anchored_end));
        } else if (_sudo_sc instanceof Err) {
          const msg = _sudo_sc.error;
          return new Err(dup(msg));
        }
      }
    }
    return new Ok(new ParseResult(dup(branches)));
  }
  function append_atom_state(atom, out_index, states) {
    {
      const _sudo_sc = atom;
      if (_sudo_sc instanceof Atom_Lit) {
        const ch = _sudo_sc.ch;
        states.push(new NfaState_CharLit(ch, out_index));
      } else if (_sudo_sc instanceof Atom_Wildcard) {
        let nl = [[10n, 10n]];
        states.push(new NfaState_CharClass(true, dup(nl), out_index));
      } else if (_sudo_sc instanceof Atom_Class) {
        const negate = _sudo_sc.negate;
        const ranges = dup(_sudo_sc.ranges);
        states.push(new NfaState_CharClass(negate, dup(ranges), out_index));
      }
    }
    return [chk(globalThis.BigInt(states.length) - 1n), states];
  }
  function build_fragment(item, out_index, states) {
    item = dup(item);
    {
      const _sudo_sc = item.quant;
      if (_sudo_sc instanceof Quant_Once) {
        let _sudo_h0;
        [_sudo_h0, states] = append_atom_state(item.atom, out_index, states);
        return [_sudo_h0, states];
      } else if (_sudo_sc instanceof Quant_Opt) {
        let atom_idx;
        [atom_idx, states] = append_atom_state(item.atom, out_index, states);
        states.push(new NfaState_Split(atom_idx, out_index));
        return [chk(globalThis.BigInt(states.length) - 1n), states];
      } else if (_sudo_sc instanceof Quant_Star) {
        let split_idx = globalThis.BigInt(states.length);
        states.push(new NfaState_Match());
        let atom_idx;
        [atom_idx, states] = append_atom_state(item.atom, split_idx, states);
        put(states, split_idx, new NfaState_Split(atom_idx, out_index));
        return [split_idx, states];
      } else if (_sudo_sc instanceof Quant_Plus) {
        let split_idx = globalThis.BigInt(states.length);
        states.push(new NfaState_Match());
        let atom_idx;
        [atom_idx, states] = append_atom_state(item.atom, split_idx, states);
        put(states, split_idx, new NfaState_Split(atom_idx, out_index));
        return [atom_idx, states];
      }
    }
  }
  function build_nfa(pr) {
    pr = dup(pr);
    let states = [];
    states.push(new NfaState_Match());
    let branch_starts = [];
    for (const br of pr.branches.slice()) {
      let next_index = 0n;
      let k = chk(globalThis.BigInt(br.items.length) - 1n);
      while (k >= 0n) {
        [next_index, states] = build_fragment(at(br.items, k), next_index, states);
        k = chk(k - 1n);
      }
      branch_starts.push([next_index, br.anchored_start, br.anchored_end]);
    }
    let passes = [];
    let combos = [[false, false], [true, false], [false, true], [true, true]];
    for (const combo of combos.slice()) {
      let ca;
      let ce;
      [ca, ce] = combo;
      let members = [];
      for (const bs of branch_starts.slice()) {
        let idx2;
        let a;
        let e;
        [idx2, a, e] = bs;
        if (a === ca && e === ce) {
          members.push(idx2);
        }
      }
      if (globalThis.BigInt(members.length) === 0n) {
        continue;
      }
      if (globalThis.BigInt(members.length) === 1n) {
        passes.push([at(members, 0n), ca, ce]);
      } else {
        let cur = at(members, chk(globalThis.BigInt(members.length) - 1n));
        let j = chk(globalThis.BigInt(members.length) - 2n);
        while (j >= 0n) {
          states.push(new NfaState_Split(at(members, j), cur));
          cur = chk(globalThis.BigInt(states.length) - 1n);
          j = chk(j - 1n);
        }
        passes.push([cur, ca, ce]);
      }
    }
    return new CompiledPattern(dup(states), dup(passes));
  }
  function compile_pattern(pattern, is_glob) {
    pattern = dup(pattern);
    let pr = parse_pattern(pattern, is_glob);
    {
      const _sudo_sc = pr;
      if (_sudo_sc instanceof Ok) {
        const parsed = _sudo_sc.value;
        return new Ok(build_nfa(parsed));
      } else if (_sudo_sc instanceof Err) {
        const msg = _sudo_sc.error;
        return new Err(dup(msg));
      }
    }
  }
  function add_state(states, idx2, list, visited) {
    states = dup(states);
    if (at(visited, idx2)) {
      return [list, visited];
    }
    put(visited, idx2, true);
    {
      const _sudo_sc = at(states, idx2);
      if (_sudo_sc instanceof NfaState_Split) {
        const o1 = _sudo_sc.out1;
        const o2 = _sudo_sc.out2;
        [list, visited] = add_state(states, o1, list, visited);
        [list, visited] = add_state(states, o2, list, visited);
      } else if (_sudo_sc instanceof NfaState_CharLit) {
        const ch = _sudo_sc.ch;
        const out = _sudo_sc.out;
        list.push(idx2);
      } else if (_sudo_sc instanceof NfaState_CharClass) {
        const neg2 = _sudo_sc.negate;
        const rng = dup(_sudo_sc.ranges);
        const out = _sudo_sc.out;
        list.push(idx2);
      } else if (_sudo_sc instanceof NfaState_Match) {
        list.push(idx2);
      }
    }
    return [list, visited];
  }
  function is_match_state(s) {
    {
      const _sudo_sc = s;
      if (_sudo_sc instanceof NfaState_Match) {
        return true;
      } else if (_sudo_sc instanceof NfaState_CharLit) {
        const a = _sudo_sc.ch;
        const b = _sudo_sc.out;
        return false;
      } else if (_sudo_sc instanceof NfaState_CharClass) {
        const c = _sudo_sc.negate;
        const d = dup(_sudo_sc.ranges);
        const e = _sudo_sc.out;
        return false;
      } else if (_sudo_sc instanceof NfaState_Split) {
        const f = _sudo_sc.out1;
        const g = _sudo_sc.out2;
        return false;
      }
    }
  }
  function run_nfa_single(states, start, anchored_start, anchored_end, input2, ignore_case) {
    states = dup(states);
    input2 = dup(input2);
    let n = globalThis.BigInt(input2.length);
    let nstates = globalThis.BigInt(states.length);
    let clist = [];
    let visited = filled(nstates, false);
    if (anchored_start) {
      [clist, visited] = add_state(states, start, clist, visited);
    }
    const _sudo_from_i = 0n;
    const _sudo_to_i = n;
    for (let i = _sudo_from_i; i <= _sudo_to_i; i += 1n) {
      if (!anchored_start) {
        [clist, visited] = add_state(states, start, clist, visited);
      }
      for (const s of clist.slice()) {
        if (is_match_state(at(states, s))) {
          if (!anchored_end || i === n) {
            return true;
          }
        }
      }
      if (i === n) {
        break;
      }
      let c = at(input2, i);
      let nlist = [];
      let nvisited = filled(nstates, false);
      for (const s of clist.slice()) {
        {
          const _sudo_sc = at(states, s);
          if (_sudo_sc instanceof NfaState_CharLit) {
            const ch = _sudo_sc.ch;
            const out = _sudo_sc.out;
            if (ci_eq(ch, c, ignore_case)) {
              [nlist, nvisited] = add_state(states, out, nlist, nvisited);
            }
          } else if (_sudo_sc instanceof NfaState_CharClass) {
            const negate = _sudo_sc.negate;
            const ranges = dup(_sudo_sc.ranges);
            const out = _sudo_sc.out;
            if (class_matches(negate, ranges, c, ignore_case)) {
              [nlist, nvisited] = add_state(states, out, nlist, nvisited);
            }
          } else if (_sudo_sc instanceof NfaState_Split) {
            const o1 = _sudo_sc.out1;
            const o2 = _sudo_sc.out2;
          } else if (_sudo_sc instanceof NfaState_Match) {
          }
        }
      }
      clist = dup(nlist);
      visited = dup(nvisited);
    }
    return false;
  }
  function run_nfa(cp, input2, ignore_case) {
    cp = dup(cp);
    input2 = dup(input2);
    for (const p of cp.passes.slice()) {
      let start;
      let astart;
      let aend;
      [start, astart, aend] = p;
      if (run_nfa_single(cp.states, start, astart, aend, input2, ignore_case)) {
        return true;
      }
    }
    return false;
  }
  function regex_search(pattern, input2, ignore_case) {
    pattern = dup(pattern);
    input2 = dup(input2);
    let cp = compile_pattern(pattern, false);
    {
      const _sudo_sc = cp;
      if (_sudo_sc instanceof Ok) {
        const compiled = _sudo_sc.value;
        return new Ok(run_nfa(compiled, input2, ignore_case));
      } else if (_sudo_sc instanceof Err) {
        const msg = _sudo_sc.error;
        return new Err(dup(msg));
      }
    }
  }
  function regex_is_valid(pattern) {
    pattern = dup(pattern);
    let cp = compile_pattern(pattern, false);
    return is_ok(cp);
  }
  function glob_match(pattern, input2, ignore_case) {
    pattern = dup(pattern);
    input2 = dup(input2);
    let cp = compile_pattern(pattern, true);
    let compiled = dup(unwrap(cp));
    return run_nfa(compiled, input2, ignore_case);
  }

  // _sudo/_craft_impl.mjs
  var Element = class {
    static _sudoKind = ["r", "Element"];
    static _sudoFields = ["name", "emoji", "first"];
    constructor(name, emoji, first) {
      this.name = name;
      this.emoji = emoji;
      this.first = first;
    }
  };
  var RecipeStep = class {
    static _sudoKind = ["r", "RecipeStep"];
    static _sudoFields = ["a", "b", "result"];
    constructor(a, b, result) {
      this.a = a;
      this.b = b;
      this.result = result;
    }
  };
  var RecipeResult_NotFound = class {
    static _sudoKind = ["e", "RecipeResult.NotFound"];
    static _sudoFields = [];
    constructor() {
    }
  };
  var RecipeResult_IsBase = class {
    static _sudoKind = ["e", "RecipeResult.IsBase"];
    static _sudoFields = ["name"];
    constructor(name) {
      this.name = name;
    }
  };
  var RecipeResult_NoRecipe = class {
    static _sudoKind = ["e", "RecipeResult.NoRecipe"];
    static _sudoFields = ["name"];
    constructor(name) {
      this.name = name;
    }
  };
  var RecipeResult_Unreachable = class {
    static _sudoKind = ["e", "RecipeResult.Unreachable"];
    static _sudoFields = ["name"];
    constructor(name) {
      this.name = name;
    }
  };
  var RecipeResult_Steps = class {
    static _sudoKind = ["e", "RecipeResult.Steps"];
    static _sudoFields = ["target", "steps"];
    constructor(target, steps) {
      this.target = target;
      this.steps = steps;
    }
  };
  function is_base_element(name) {
    name = dup(name);
    return eq(name, txt("Water")) || eq(name, txt("Fire")) || eq(name, txt("Wind")) || eq(name, txt("Earth"));
  }
  function upper_char(c) {
    if (c >= 97n && c <= 122n) {
      return chk(c - 32n);
    }
    return c;
  }
  function lower_char(c) {
    if (c >= 65n && c <= 90n) {
      return chk(c + 32n);
    }
    return c;
  }
  function is_alpha_ascii(c) {
    return c >= 97n && c <= 122n || c >= 65n && c <= 90n;
  }
  function is_word_char(c) {
    return is_alpha_ascii(c) || c >= 48n && c <= 57n || c === 95n;
  }
  function is_ascii_space(c) {
    return c === 32n || c === 9n || c === 10n || c === 13n || c === 11n || c === 12n;
  }
  function strip_spaces(s) {
    s = dup(s);
    let start = 0n;
    let end = globalThis.BigInt(s.length);
    while (start < end && is_ascii_space(at(s, start))) {
      start = chk(start + 1n);
    }
    while (end > start && is_ascii_space(at(s, chk(end - 1n)))) {
      end = chk(end - 1n);
    }
    let out = txt("");
    const _sudo_from_i = start;
    const _sudo_to_i = chk(end - 1n);
    for (let i = _sudo_from_i; i <= _sudo_to_i; i += 1n) {
      out.push(at(s, i));
    }
    return dup(out);
  }
  function strip_prefix_one(s) {
    s = dup(s);
    let out = txt("");
    const _sudo_from_i = 1n;
    const _sudo_to_i = chk(globalThis.BigInt(s.length) - 1n);
    for (let i = _sudo_from_i; i <= _sudo_to_i; i += 1n) {
      out.push(at(s, i));
    }
    return dup(out);
  }
  function text_slice(s, start, end) {
    s = dup(s);
    let out = txt("");
    let i = start;
    while (i < end && i < globalThis.BigInt(s.length)) {
      out.push(at(s, i));
      i = chk(i + 1n);
    }
    return dup(out);
  }
  function text_from(s, start) {
    s = dup(s);
    return text_slice(s, start, globalThis.BigInt(s.length));
  }
  function title_case(s) {
    s = dup(s);
    let out = txt("");
    let prev_cased = false;
    for (const c of s.slice()) {
      if (is_alpha_ascii(c)) {
        if (prev_cased) {
          out.push(lower_char(c));
        } else {
          out.push(upper_char(c));
        }
        prev_cased = true;
      } else {
        out.push(c);
        prev_cased = false;
      }
    }
    return dup(out);
  }
  function get_by_name(elements, name) {
    elements = dup(elements);
    name = dup(name);
    for (const e of elements.slice()) {
      if (eq(e.name, name)) {
        return new Some(dup(e));
      }
    }
    return NONE;
  }
  function resolve_element(elements, name) {
    elements = dup(elements);
    name = dup(name);
    let found = get_by_name(elements, name);
    if (is_some(found)) {
      return dup(unwrap(found));
    }
    let title = title_case(strip_spaces(name));
    if (!eq(title, name)) {
      let found2 = get_by_name(elements, title);
      if (is_some(found2)) {
        return dup(unwrap(found2));
      }
    }
    return new Element(dup(title), txt(""), false);
  }
  function pair_key(a, b) {
    a = dup(a);
    b = dup(b);
    if (lex_compare(a, b) <= 0n) {
      return [dup(a), dup(b)];
    }
    return [dup(b), dup(a)];
  }
  function pair_in_list(pairs, pair) {
    pairs = dup(pairs);
    pair = dup(pair);
    for (const p of pairs.slice()) {
      if (eq(p, pair)) {
        return true;
      }
    }
    return false;
  }
  function record_recipe(recipes, result_name, a_name, b_name) {
    result_name = dup(result_name);
    a_name = dup(a_name);
    b_name = dup(b_name);
    let pair = pair_key(a_name, b_name);
    let existing = dup(get_or(recipes.get_opt(result_name), []));
    if (!pair_in_list(existing, pair)) {
      existing.push(dup(pair));
      recipes.set(result_name, dup(existing));
    }
    return recipes;
  }
  function exhaust_pairs(matches, all_elements) {
    matches = dup(matches);
    all_elements = dup(all_elements);
    let seen = new SudoSet();
    let pairs = [];
    for (const target of matches.slice()) {
      for (const other of all_elements.slice()) {
        if (eq(other.name, target.name)) {
          continue;
        }
        let key = pair_key(target.name, other.name);
        let ka;
        let kb;
        [ka, kb] = dup(key);
        let key_text = dup(dup(ka.concat(txt("\0"))).concat(kb));
        if (seen.has(key_text)) {
          continue;
        }
        seen.add(key_text);
        pairs.push([dup(target), dup(other)]);
      }
    }
    return dup(pairs);
  }
  function sort_texts(items) {
    const _sudo_from_j = 1n;
    const _sudo_to_j = chk(globalThis.BigInt(items.length) - 1n);
    for (let j = _sudo_from_j; j <= _sudo_to_j; j += 1n) {
      let i = j;
      while (i > 0n && lex_compare(at(items, i), at(items, chk(i - 1n))) < 0n) {
        swap(items, i, chk(i - 1n));
        i = chk(i - 1n);
      }
    }
    return items;
  }
  function sorted_text_list(items) {
    items = dup(items);
    let out = dup(items);
    out = sort_texts(out);
    return dup(out);
  }
  function is_available(n, visited, recipes) {
    n = dup(n);
    visited = dup(visited);
    recipes = dup(recipes);
    if (visited.has(n)) {
      return true;
    }
    if (is_base_element(n)) {
      return true;
    }
    let entry = recipes.get_opt(n);
    if (is_none(entry)) {
      return true;
    }
    return globalThis.BigInt(unwrap(entry).length) === 0n;
  }
  function compute_layers(target, recipes) {
    target = dup(target);
    recipes = dup(recipes);
    let parent = new SudoMap();
    let visited = new SudoSet();
    visited.add(txt("Water"));
    visited.add(txt("Fire"));
    visited.add(txt("Wind"));
    visited.add(txt("Earth"));
    let found = false;
    while (!found) {
      let new_names = [];
      let new_pairs = new SudoMap();
      let keys = sorted_text_list(recipes.keys_list());
      for (const result_name of keys.slice()) {
        if (visited.has(result_name)) {
          continue;
        }
        if (new_pairs.has(result_name)) {
          continue;
        }
        let pairs = dup(recipes.get(result_name));
        for (const pair of pairs.slice()) {
          let pa;
          let pb;
          [pa, pb] = dup(pair);
          let a_ok = is_available(pa, visited, recipes);
          let b_ok = is_available(pb, visited, recipes);
          if (a_ok && b_ok) {
            new_pairs.set(result_name, dup(pair));
            new_names.push(dup(result_name));
            if (eq(result_name, target)) {
              found = true;
            }
            break;
          }
        }
      }
      if (globalThis.BigInt(new_names.length) === 0n) {
        break;
      }
      for (const rn of new_names.slice()) {
        let pr = dup(new_pairs.get(rn));
        parent.set(rn, dup(pr));
        visited.add(rn);
      }
    }
    return [found, dup(parent)];
  }
  function backtrack_steps(target, parent) {
    target = dup(target);
    parent = dup(parent);
    let resolved = new SudoSet();
    resolved.add(txt("Water"));
    resolved.add(txt("Fire"));
    resolved.add(txt("Wind"));
    resolved.add(txt("Earth"));
    let to_resolve = [dup(target)];
    let steps = [];
    while (globalThis.BigInt(to_resolve.length) > 0n) {
      let nm = pop(to_resolve);
      if (resolved.has(nm)) {
        continue;
      }
      if (!parent.has(nm)) {
        resolved.add(nm);
        continue;
      }
      let pr = dup(parent.get(nm));
      let a;
      let b;
      [a, b] = dup(pr);
      let deferred = false;
      for (const dep of [dup(a), dup(b)].slice()) {
        if (resolved.has(dep)) {
          continue;
        }
        if (!parent.has(dep) && !is_base_element(dep)) {
          resolved.add(dep);
          continue;
        }
        to_resolve.push(dup(nm));
        to_resolve.push(dup(dep));
        deferred = true;
        break;
      }
      if (!deferred) {
        steps.push(new RecipeStep(dup(a), dup(b), dup(nm)));
        resolved.add(nm);
      }
    }
    return dup(steps);
  }
  function trace_recipe(elements, recipes, name) {
    elements = dup(elements);
    recipes = dup(recipes);
    name = dup(name);
    let stripped = strip_spaces(name);
    let elem = get_by_name(elements, stripped);
    if (is_none(elem)) {
      elem = get_by_name(elements, title_case(stripped));
    }
    if (is_none(elem)) {
      return new RecipeResult_NotFound();
    }
    let target = dup(unwrap(elem).name);
    if (is_base_element(target)) {
      return new RecipeResult_IsBase(dup(target));
    }
    let target_pairs = dup(get_or(recipes.get_opt(target), []));
    if (globalThis.BigInt(target_pairs.length) === 0n) {
      return new RecipeResult_NoRecipe(dup(target));
    }
    let found;
    let parent;
    [found, parent] = compute_layers(target, recipes);
    if (!found) {
      return new RecipeResult_Unreachable(dup(target));
    }
    let steps = backtrack_steps(target, parent);
    return new RecipeResult_Steps(dup(target), dup(steps));
  }
  function included_element_names(recipes) {
    recipes = dup(recipes);
    let included = new SudoSet();
    included.add(txt("Water"));
    included.add(txt("Fire"));
    included.add(txt("Wind"));
    included.add(txt("Earth"));
    let keys = sorted_text_list(recipes.keys_list());
    for (const name of keys.slice()) {
      let pairs = dup(recipes.get(name));
      if (globalThis.BigInt(pairs.length) > 0n) {
        included.add(name);
      }
    }
    let changed = true;
    while (changed) {
      changed = false;
      let names = sorted_text_list(included.items_list());
      for (const nm of names.slice()) {
        if (!recipes.has(nm)) {
          continue;
        }
        for (const pair of recipes.get(nm).slice()) {
          let pa;
          let pb;
          [pa, pb] = dup(pair);
          if (!included.has(pa)) {
            included.add(pa);
            changed = true;
          }
          if (!included.has(pb)) {
            included.add(pb);
            changed = true;
          }
        }
      }
    }
    return dup(included);
  }
  function orphan_candidates(elements, recipes) {
    elements = dup(elements);
    recipes = dup(recipes);
    let included = included_element_names(recipes);
    let out = [];
    for (const e of elements.slice()) {
      if (!included.has(e.name)) {
        out.push(dup(e));
      }
    }
    return dup(out);
  }
  function export_elements(elements, recipes) {
    elements = dup(elements);
    recipes = dup(recipes);
    let included = included_element_names(recipes);
    let out = [];
    for (const e of elements.slice()) {
      if (included.has(e.name)) {
        out.push(dup(e));
      }
    }
    return dup(out);
  }
  function parse_query_filter(query) {
    query = dup(query);
    let q = strip_spaces(query);
    let exclude = false;
    let only_new = false;
    if (globalThis.BigInt(q.length) > 0n && at(q, 0n) === 33n) {
      exclude = true;
      q = strip_prefix_one(q);
    } else if (globalThis.BigInt(q.length) > 0n && at(q, 0n) === 94n) {
      only_new = true;
      q = strip_prefix_one(q);
    }
    return [dup(q), exclude, only_new];
  }
  function is_delimited_regex(pattern) {
    pattern = dup(pattern);
    let p = strip_spaces(pattern);
    return globalThis.BigInt(p.length) >= 2n && at(p, 0n) === 47n && at(p, chk(globalThis.BigInt(p.length) - 1n)) === 47n;
  }
  function is_glob_trigger(pattern) {
    pattern = dup(pattern);
    for (const c of pattern.slice()) {
      if (c === 42n || c === 63n || c === 91n || c === 93n) {
        return true;
      }
    }
    return false;
  }
  function element_matches_pattern(name, pattern) {
    name = dup(name);
    pattern = dup(pattern);
    pattern = strip_spaces(pattern);
    if (globalThis.BigInt(pattern.length) === 0n) {
      return [false, NONE];
    }
    if (is_delimited_regex(pattern)) {
      let body2 = text_slice(pattern, 1n, chk(globalThis.BigInt(pattern.length) - 1n));
      if (globalThis.BigInt(body2.length) === 0n) {
        return [false, NONE];
      }
      {
        const _sudo_sc = regex_search(body2, name, true);
        if (_sudo_sc instanceof Err) {
          const msg = _sudo_sc.error;
          sudo_assert(globalThis.BigInt(msg.length) >= 0n, 427);
          return [false, new Some(txt("Invalid regex pattern"))];
        } else if (_sudo_sc instanceof Ok) {
          const matched = _sudo_sc.value;
          return [matched, NONE];
        }
      }
    }
    if (is_glob_trigger(pattern)) {
      return [glob_match(pattern, name, true), NONE];
    }
    return [contains(to_lower(name), to_lower(pattern)), NONE];
  }
  function match_elements(elements, query) {
    elements = dup(elements);
    query = dup(query);
    let empty = [];
    if (globalThis.BigInt(query.length) > 512n) {
      return [dup(empty), new Some(txt("Query too long (max 512 characters)"))];
    }
    let q;
    let exclude;
    let only_new;
    [q, exclude, only_new] = parse_query_filter(query);
    if (globalThis.BigInt(strip_spaces(q).length) === 0n) {
      if (exclude) {
        return [dup(elements), NONE];
      }
      if (only_new) {
        let firsts = [];
        for (const e of elements.slice()) {
          if (e.first) {
            firsts.push(dup(e));
          }
        }
        return [dup(firsts), NONE];
      }
      return [dup(empty), NONE];
    }
    let matches = [];
    for (const e of elements.slice()) {
      let matched;
      let err;
      [matched, err] = element_matches_pattern(e.name, q);
      if (is_some(err)) {
        return [dup(empty), err];
      }
      if (exclude) {
        if (!matched) {
          matches.push(dup(e));
        }
      } else if (matched) {
        matches.push(dup(e));
      }
    }
    if (only_new) {
      let filtered = [];
      for (const e of matches.slice()) {
        if (e.first) {
          filtered.push(dup(e));
        }
      }
      return [dup(filtered), NONE];
    }
    return [dup(matches), NONE];
  }
  function slash_args(line, command) {
    line = dup(line);
    command = dup(command);
    if (eq(line, command)) {
      return new Some(txt(""));
    }
    let prefix = dup(command.concat(txt(" ")));
    if (starts_with(line, prefix)) {
      return new Some(text_from(line, globalThis.BigInt(prefix.length)));
    }
    return NONE;
  }
  function is_local_command(line) {
    line = dup(line);
    if (eq(line, txt("/help")) || eq(line, txt("/list")) || eq(line, txt("/history")) || eq(line, txt("/clear")) || eq(line, txt("/queue"))) {
      return true;
    }
    if (eq(line, txt("/unfilled")) || starts_with(line, txt("/unfilled "))) {
      return true;
    }
    if (eq(line, txt("/search")) || starts_with(line, txt("/search "))) {
      return true;
    }
    if (eq(line, txt("/recipe")) || starts_with(line, txt("/recipe "))) {
      return true;
    }
    return false;
  }
  function is_slash_command_attempt(line) {
    line = dup(line);
    if (globalThis.BigInt(line.length) === 0n || at(line, 0n) !== 47n) {
      return false;
    }
    if (globalThis.BigInt(line.length) >= 3n) {
      let i = 1n;
      while (i < globalThis.BigInt(line.length) && at(line, i) !== 47n) {
        i = chk(i + 1n);
      }
      if (i > 1n && i < globalThis.BigInt(line.length) && at(line, i) === 47n) {
        return false;
      }
    }
    if (globalThis.BigInt(line.length) >= 2n && is_word_char(at(line, 1n))) {
      return true;
    }
    return false;
  }
  function contains_plus_ws_pipe(s) {
    s = dup(s);
    let i = 0n;
    while (i < globalThis.BigInt(s.length)) {
      if (at(s, i) === 43n) {
        let j = chk(i + 1n);
        if (j < globalThis.BigInt(s.length) && is_ascii_space(at(s, j))) {
          while (j < globalThis.BigInt(s.length) && is_ascii_space(at(s, j))) {
            j = chk(j + 1n);
          }
          if (j < globalThis.BigInt(s.length) && at(s, j) === 124n) {
            return true;
          }
        }
      }
      i = chk(i + 1n);
    }
    return false;
  }
  function ends_with_space_plus(s) {
    s = dup(s);
    let t = strip_spaces(s);
    let end = globalThis.BigInt(s.length);
    while (end > 0n && is_ascii_space(at(s, chk(end - 1n)))) {
      end = chk(end - 1n);
    }
    if (end < 2n) {
      return false;
    }
    return at(s, chk(end - 2n)) === 32n && at(s, chk(end - 1n)) === 43n;
  }
  function classify_command_line(line) {
    line = dup(line);
    line = strip_spaces(line);
    if (globalThis.BigInt(line.length) === 0n) {
      return NONE;
    }
    let cmds = [txt("/permute"), txt("/permutate"), txt("/import"), txt("/fill"), txt("/prune"), txt("/export"), txt("/exhaust"), txt("/combine"), txt("/crawl"), txt("/with"), txt("/cross")];
    for (const cmd of cmds.slice()) {
      let rest_opt = slash_args(line, cmd);
      if (is_some(rest_opt)) {
        let kind = strip_prefix_one(cmd);
        return new Some([dup(kind), dup(unwrap(rest_opt))]);
      }
    }
    if (is_slash_command_attempt(line)) {
      return NONE;
    }
    if (contains_plus_ws_pipe(line)) {
      return new Some([txt("bad+|"), dup(line)]);
    }
    if (contains(line, txt(" ++ "))) {
      return new Some([txt("++"), dup(line)]);
    }
    if (contains(line, txt("+|"))) {
      return new Some([txt("+|"), dup(line)]);
    }
    if (contains(line, txt(" * "))) {
      return new Some([txt("*"), dup(line)]);
    }
    if (contains(line, txt(" + ")) || ends_with_space_plus(line)) {
      return new Some([txt("+"), dup(line)]);
    }
    return NONE;
  }
  function parse_two_elements(rest) {
    rest = dup(rest);
    rest = strip_spaces(rest);
    let i = 0n;
    while (i < globalThis.BigInt(rest.length) && !is_ascii_space(at(rest, i))) {
      i = chk(i + 1n);
    }
    if (i >= globalThis.BigInt(rest.length)) {
      return NONE;
    }
    let first = strip_spaces(text_slice(rest, 0n, i));
    let second = strip_spaces(text_from(rest, i));
    if (globalThis.BigInt(first.length) === 0n || globalThis.BigInt(second.length) === 0n) {
      return NONE;
    }
    return new Some([dup(first), dup(second)]);
  }
  function parse_with_args(rest) {
    rest = dup(rest);
    return parse_two_elements(rest);
  }
  function split_two_positional_args(rest) {
    rest = dup(rest);
    rest = strip_spaces(rest);
    if (globalThis.BigInt(rest.length) === 0n) {
      return NONE;
    }
    let tokens = [];
    let i = 0n;
    let n = globalThis.BigInt(rest.length);
    while (i < n && globalThis.BigInt(tokens.length) < 2n) {
      while (i < n && is_ascii_space(at(rest, i))) {
        i = chk(i + 1n);
      }
      if (i >= n) {
        break;
      }
      let token = txt("");
      if (at(rest, i) === 47n) {
        let j = chk(i + 1n);
        let found_close = false;
        while (j < n) {
          if (at(rest, j) === 47n) {
            found_close = true;
            break;
          }
          j = chk(j + 1n);
        }
        if (!found_close) {
          j = i;
          while (j < n && !is_ascii_space(at(rest, j))) {
            j = chk(j + 1n);
          }
          token = text_slice(rest, i, j);
          i = j;
        } else {
          token = text_slice(rest, i, chk(j + 1n));
          i = chk(j + 1n);
        }
      } else {
        let j = i;
        while (j < n && !is_ascii_space(at(rest, j))) {
          j = chk(j + 1n);
        }
        token = text_slice(rest, i, j);
        i = j;
      }
      token = strip_spaces(token);
      if (globalThis.BigInt(token.length) > 0n) {
        tokens.push(dup(token));
      }
    }
    if (globalThis.BigInt(tokens.length) !== 2n) {
      return NONE;
    }
    while (i < n && is_ascii_space(at(rest, i))) {
      i = chk(i + 1n);
    }
    if (i < n) {
      return NONE;
    }
    return new Some([dup(at(tokens, 0n)), dup(at(tokens, 1n))]);
  }
  function parse_cross_queries(rest) {
    rest = dup(rest);
    return split_two_positional_args(rest);
  }
  function slash_combine_crawl_pipe_error(rest) {
    rest = dup(rest);
    let msg = txt("  Use <element> +| <query> (no space between + and |). Type /help for commands.");
    if (contains_plus_ws_pipe(rest)) {
      return new Some(dup(msg));
    }
    let parsed = parse_two_elements(rest);
    if (is_some(parsed)) {
      let first;
      let second;
      [first, second] = dup(unwrap(parsed));
      if (globalThis.BigInt(second.length) > 0n && at(second, 0n) === 124n) {
        return new Some(dup(msg));
      }
    }
    return NONE;
  }
  function slash_combine_crawl_operator_error(rest, kind) {
    rest = dup(rest);
    kind = dup(kind);
    if (!contains(rest, txt(" + "))) {
      return NONE;
    }
    let idx2 = unwrap(index_of(rest, txt(" + ")));
    let left = text_slice(rest, 0n, idx2);
    let right = text_from(rest, chk(idx2 + 3n));
    let positional = dup(dup(dup(dup(dup(txt("/").concat(kind)).concat(txt(" "))).concat(strip_spaces(left))).concat(txt(" "))).concat(strip_spaces(right)));
    return new Some(dup(dup(dup(dup(dup(dup(txt("  Slash /").concat(kind)).concat(txt(" uses positional args, not +. Try "))).concat(strip_spaces(rest))).concat(txt(" (shorthand) or "))).concat(positional)).concat(txt("."))));
  }
  function slash_cross_operator_error(rest) {
    rest = dup(rest);
    if (!contains(rest, txt(" * "))) {
      return NONE;
    }
    let idx2 = unwrap(index_of(rest, txt(" * ")));
    let left = text_slice(rest, 0n, idx2);
    let right = text_from(rest, chk(idx2 + 3n));
    let positional = dup(dup(dup(txt("/cross ").concat(strip_spaces(left))).concat(txt(" "))).concat(strip_spaces(right)));
    return new Some(dup(dup(dup(dup(txt("  Slash /cross uses positional args, not *. Try ").concat(strip_spaces(rest))).concat(txt(" (shorthand) or "))).concat(positional)).concat(txt("."))));
  }
  function validate_query_at_enqueue(query) {
    query = dup(query);
    if (globalThis.BigInt(query.length) > 512n) {
      return new Some(txt("  Query too long (max 512 characters)"));
    }
    let q;
    let qf_exclude;
    let qf_only_new;
    [q, qf_exclude, qf_only_new] = parse_query_filter(query);
    sudo_assert(qf_exclude === qf_exclude && qf_only_new === qf_only_new, 655);
    if (!is_delimited_regex(q)) {
      return NONE;
    }
    let body2 = text_slice(q, 1n, chk(globalThis.BigInt(q.length) - 1n));
    if (!regex_is_valid(body2)) {
      return new Some(txt("  Invalid regex pattern"));
    }
    return NONE;
  }
  function first_ws_token(s) {
    s = dup(s);
    s = strip_spaces(s);
    let i = 0n;
    while (i < globalThis.BigInt(s.length) && !is_ascii_space(at(s, i))) {
      i = chk(i + 1n);
    }
    return text_slice(s, 0n, i);
  }
  function split_once(s, sep) {
    s = dup(s);
    sep = dup(sep);
    let idx_opt = index_of(s, sep);
    if (is_none(idx_opt)) {
      return [dup(s), txt("")];
    }
    let idx2 = unwrap(idx_opt);
    return [text_slice(s, 0n, idx2), text_from(s, chk(idx2 + globalThis.BigInt(sep.length)))];
  }
  function rsplit_once_space_plus(s) {
    s = dup(s);
    let last = neg(1n);
    let i = 0n;
    while (chk(i + 1n) < globalThis.BigInt(s.length)) {
      if (at(s, i) === 32n && at(s, chk(i + 1n)) === 43n) {
        last = i;
      }
      i = chk(i + 1n);
    }
    if (last < 0n) {
      return dup(s);
    }
    return text_slice(s, 0n, last);
  }
  function validate_command_line(line) {
    line = dup(line);
    let classified = classify_command_line(line);
    if (is_none(classified)) {
      if (is_slash_command_attempt(line)) {
        let cmd = first_ws_token(line);
        return new Some(dup(dup(txt("  Unknown command: ").concat(cmd)).concat(txt(". Type /help for commands."))));
      }
      return new Some(txt("  Unknown input. Type /help for commands."));
    }
    let kind;
    let payload;
    [kind, payload] = dup(unwrap(classified));
    if (eq(kind, txt("bad+|"))) {
      return new Some(txt("  Use <element> +| <query> (no space between + and |). Type /help for commands."));
    }
    if (eq(kind, txt("permute")) || eq(kind, txt("permutate")) || eq(kind, txt("exhaust")) || eq(kind, txt("import"))) {
      if (globalThis.BigInt(strip_spaces(payload).length) === 0n) {
        if (eq(kind, txt("import"))) {
          return new Some(txt("  Usage: /import <element>"));
        }
        return new Some(dup(dup(txt("  Usage: /").concat(kind)).concat(txt(" <query>"))));
      }
      if (eq(kind, txt("import"))) {
        return NONE;
      }
      return validate_query_at_enqueue(strip_spaces(payload));
    }
    if (eq(kind, txt("export")) || eq(kind, txt("fill")) || eq(kind, txt("prune"))) {
      return NONE;
    }
    if (eq(kind, txt("combine")) || eq(kind, txt("crawl"))) {
      let pipe_err = slash_combine_crawl_pipe_error(payload);
      if (is_some(pipe_err)) {
        return pipe_err;
      }
      let op_err = slash_combine_crawl_operator_error(payload, kind);
      if (is_some(op_err)) {
        return op_err;
      }
      if (is_none(parse_two_elements(payload))) {
        return new Some(dup(dup(txt("  Usage: /").concat(kind)).concat(txt(" <element> <element>"))));
      }
      return NONE;
    }
    if (eq(kind, txt("with"))) {
      let parsed = parse_with_args(payload);
      if (is_none(parsed)) {
        return new Some(txt("  Usage: /with <element> <query>"));
      }
      let with_elem;
      let query;
      [with_elem, query] = dup(unwrap(parsed));
      sudo_assert_eq(with_elem, with_elem, 725);
      return validate_query_at_enqueue(query);
    }
    if (eq(kind, txt("cross"))) {
      let op_err = slash_cross_operator_error(payload);
      if (is_some(op_err)) {
        return op_err;
      }
      let parsed = parse_cross_queries(payload);
      if (is_none(parsed)) {
        return new Some(txt("  Usage: /cross <query> <query>"));
      }
      let left_q;
      let right_q;
      [left_q, right_q] = dup(unwrap(parsed));
      let err = validate_query_at_enqueue(left_q);
      if (is_some(err)) {
        return err;
      }
      return validate_query_at_enqueue(right_q);
    }
    if (eq(kind, txt("++"))) {
      let left;
      let right;
      [left, right] = split_once(payload, txt(" ++ "));
      if (globalThis.BigInt(strip_spaces(left).length) === 0n || globalThis.BigInt(strip_spaces(right).length) === 0n) {
        return new Some(txt("  Usage: <element> ++ <element>"));
      }
      return NONE;
    }
    if (eq(kind, txt("+|"))) {
      let left;
      let right;
      [left, right] = split_once(payload, txt("+|"));
      if (globalThis.BigInt(strip_spaces(left).length) === 0n || globalThis.BigInt(strip_spaces(right).length) === 0n) {
        return new Some(txt("  Usage: <element> +| <query>"));
      }
      return validate_query_at_enqueue(strip_spaces(right));
    }
    if (eq(kind, txt("*"))) {
      let left;
      let right;
      [left, right] = split_once(payload, txt(" * "));
      if (globalThis.BigInt(strip_spaces(left).length) === 0n || globalThis.BigInt(strip_spaces(right).length) === 0n) {
        return new Some(txt("  Usage: <query> * <query>"));
      }
      let err = validate_query_at_enqueue(strip_spaces(left));
      if (is_some(err)) {
        return err;
      }
      return validate_query_at_enqueue(strip_spaces(right));
    }
    if (eq(kind, txt("+"))) {
      let left = txt("");
      let right = txt("");
      if (contains(payload, txt(" + "))) {
        [left, right] = split_once(payload, txt(" + "));
      } else {
        left = rsplit_once_space_plus(payload);
        right = txt("");
      }
      if (globalThis.BigInt(strip_spaces(left).length) === 0n || globalThis.BigInt(strip_spaces(right).length) === 0n) {
        return new Some(txt("  Usage: <element> + <element>"));
      }
      return NONE;
    }
    return NONE;
  }
  function element_from_tuple(t) {
    t = dup(t);
    let n;
    let em;
    let f;
    [n, em, f] = dup(t);
    return new Element(dup(n), dup(em), f);
  }
  function element_to_tuple(e) {
    e = dup(e);
    return [dup(e.name), dup(e.emoji), e.first];
  }
  function elements_from_tuples(ts) {
    ts = dup(ts);
    let out = [];
    for (const t of ts.slice()) {
      out.push(element_from_tuple(t));
    }
    return dup(out);
  }
  function elements_to_tuples(es) {
    es = dup(es);
    let out = [];
    for (const e of es.slice()) {
      out.push(element_to_tuple(e));
    }
    return dup(out);
  }
  function recipe_result_to_tuple(r) {
    {
      const _sudo_sc = r;
      if (_sudo_sc instanceof RecipeResult_NotFound) {
        let empty = [];
        return [0n, txt(""), dup(empty)];
      } else if (_sudo_sc instanceof RecipeResult_IsBase) {
        const n = dup(_sudo_sc.name);
        let empty = [];
        return [1n, dup(n), dup(empty)];
      } else if (_sudo_sc instanceof RecipeResult_NoRecipe) {
        const n = dup(_sudo_sc.name);
        let empty = [];
        return [2n, dup(n), dup(empty)];
      } else if (_sudo_sc instanceof RecipeResult_Unreachable) {
        const n = dup(_sudo_sc.name);
        let empty = [];
        return [3n, dup(n), dup(empty)];
      } else if (_sudo_sc instanceof RecipeResult_Steps) {
        const t = dup(_sudo_sc.target);
        const steps = dup(_sudo_sc.steps);
        let out = [];
        for (const s of steps.slice()) {
          out.push([dup(s.a), dup(s.b), dup(s.result)]);
        }
        return [4n, dup(t), dup(out)];
      }
    }
  }
  function resolve_element_boundary(elements, name) {
    elements = dup(elements);
    name = dup(name);
    return element_to_tuple(resolve_element(elements_from_tuples(elements), name));
  }
  function trace_recipe_boundary(elements, recipes, name) {
    elements = dup(elements);
    recipes = dup(recipes);
    name = dup(name);
    let r = trace_recipe(elements_from_tuples(elements), recipes, name);
    return recipe_result_to_tuple(r);
  }
  function orphan_candidates_boundary(elements, recipes) {
    elements = dup(elements);
    recipes = dup(recipes);
    return elements_to_tuples(orphan_candidates(elements_from_tuples(elements), recipes));
  }
  function export_elements_boundary(elements, recipes) {
    elements = dup(elements);
    recipes = dup(recipes);
    return elements_to_tuples(export_elements(elements_from_tuples(elements), recipes));
  }
  function match_elements_boundary(elements, query) {
    elements = dup(elements);
    query = dup(query);
    let matches;
    let err;
    [matches, err] = match_elements(elements_from_tuples(elements), query);
    return [elements_to_tuples(matches), err];
  }
  function exhaust_pairs_boundary(matches, all_elements) {
    matches = dup(matches);
    all_elements = dup(all_elements);
    let pairs = exhaust_pairs(elements_from_tuples(matches), elements_from_tuples(all_elements));
    let out = [];
    for (const p of pairs.slice()) {
      let a;
      let b;
      [a, b] = dup(p);
      out.push([dup(a.name), dup(a.emoji), a.first, dup(b.name), dup(b.emoji), b.first]);
    }
    return dup(out);
  }

  // _sudo/craft.mjs
  function pair_key2(a, b) {
    a = host_text(a);
    b = host_text(b);
    const _r = pair_key(a, b);
    return [text_str(_r[0]), text_str(_r[1])];
  }
  function record_recipe2(recipes, result_name, a_name, b_name) {
    const _in_recipes = host_map(recipes, (_k) => host_text(_k), (_v) => host_list(_v, (_v2) => host_tuple(_v2, 2, [(_v3) => host_text(_v3), (_v3) => host_text(_v3)])));
    result_name = host_text(result_name);
    a_name = host_text(a_name);
    b_name = host_text(b_name);
    const _new_recipes = record_recipe(_in_recipes, result_name, a_name, b_name);
    writeback_map(recipes, _new_recipes, (_k) => text_str(_k), (_v) => _v.map((_v2) => [text_str(_v2[0]), text_str(_v2[1])]));
  }
  function slash_args2(line, command) {
    line = host_text(line);
    command = host_text(command);
    const _r = slash_args(line, command);
    return out_option(_r, (_v) => text_str(_v));
  }
  function is_local_command2(line) {
    line = host_text(line);
    const _r = is_local_command(line);
    return _r;
  }
  function classify_command_line2(line) {
    line = host_text(line);
    const _r = classify_command_line(line);
    return out_option(_r, (_v) => [text_str(_v[0]), text_str(_v[1])]);
  }
  function parse_two_elements2(rest) {
    rest = host_text(rest);
    const _r = parse_two_elements(rest);
    return out_option(_r, (_v) => [text_str(_v[0]), text_str(_v[1])]);
  }
  function parse_with_args2(rest) {
    rest = host_text(rest);
    const _r = parse_with_args(rest);
    return out_option(_r, (_v) => [text_str(_v[0]), text_str(_v[1])]);
  }
  function parse_cross_queries2(rest) {
    rest = host_text(rest);
    const _r = parse_cross_queries(rest);
    return out_option(_r, (_v) => [text_str(_v[0]), text_str(_v[1])]);
  }
  function slash_combine_crawl_pipe_error2(rest) {
    rest = host_text(rest);
    const _r = slash_combine_crawl_pipe_error(rest);
    return out_option(_r, (_v) => text_str(_v));
  }
  function slash_combine_crawl_operator_error2(rest, kind) {
    rest = host_text(rest);
    kind = host_text(kind);
    const _r = slash_combine_crawl_operator_error(rest, kind);
    return out_option(_r, (_v) => text_str(_v));
  }
  function slash_cross_operator_error2(rest) {
    rest = host_text(rest);
    const _r = slash_cross_operator_error(rest);
    return out_option(_r, (_v) => text_str(_v));
  }
  function validate_command_line2(line) {
    line = host_text(line);
    const _r = validate_command_line(line);
    return out_option(_r, (_v) => text_str(_v));
  }
  function resolve_element_boundary2(elements, name) {
    elements = host_list(elements, (_v) => host_tuple(_v, 3, [(_v2) => host_text(_v2), (_v2) => host_text(_v2), (_v2) => host_bool(_v2)]));
    name = host_text(name);
    const _r = resolve_element_boundary(elements, name);
    return [text_str(_r[0]), text_str(_r[1]), _r[2]];
  }
  function trace_recipe_boundary2(elements, recipes, name) {
    elements = host_list(elements, (_v) => host_tuple(_v, 3, [(_v2) => host_text(_v2), (_v2) => host_text(_v2), (_v2) => host_bool(_v2)]));
    recipes = host_map(recipes, (_k) => host_text(_k), (_v) => host_list(_v, (_v2) => host_tuple(_v2, 2, [(_v3) => host_text(_v3), (_v3) => host_text(_v3)])));
    name = host_text(name);
    const _r = trace_recipe_boundary(elements, recipes, name);
    return [int_out(_r[0]), text_str(_r[1]), _r[2].map((_v) => [text_str(_v[0]), text_str(_v[1]), text_str(_v[2])])];
  }
  function orphan_candidates_boundary2(elements, recipes) {
    elements = host_list(elements, (_v) => host_tuple(_v, 3, [(_v2) => host_text(_v2), (_v2) => host_text(_v2), (_v2) => host_bool(_v2)]));
    recipes = host_map(recipes, (_k) => host_text(_k), (_v) => host_list(_v, (_v2) => host_tuple(_v2, 2, [(_v3) => host_text(_v3), (_v3) => host_text(_v3)])));
    const _r = orphan_candidates_boundary(elements, recipes);
    return _r.map((_v) => [text_str(_v[0]), text_str(_v[1]), _v[2]]);
  }
  function export_elements_boundary2(elements, recipes) {
    elements = host_list(elements, (_v) => host_tuple(_v, 3, [(_v2) => host_text(_v2), (_v2) => host_text(_v2), (_v2) => host_bool(_v2)]));
    recipes = host_map(recipes, (_k) => host_text(_k), (_v) => host_list(_v, (_v2) => host_tuple(_v2, 2, [(_v3) => host_text(_v3), (_v3) => host_text(_v3)])));
    const _r = export_elements_boundary(elements, recipes);
    return _r.map((_v) => [text_str(_v[0]), text_str(_v[1]), _v[2]]);
  }
  function match_elements_boundary2(elements, query) {
    elements = host_list(elements, (_v) => host_tuple(_v, 3, [(_v2) => host_text(_v2), (_v2) => host_text(_v2), (_v2) => host_bool(_v2)]));
    query = host_text(query);
    const _r = match_elements_boundary(elements, query);
    return [_r[0].map((_v) => [text_str(_v[0]), text_str(_v[1]), _v[2]]), out_option(_r[1], (_v) => text_str(_v))];
  }
  function exhaust_pairs_boundary2(matches, all_elements) {
    matches = host_list(matches, (_v) => host_tuple(_v, 3, [(_v2) => host_text(_v2), (_v2) => host_text(_v2), (_v2) => host_bool(_v2)]));
    all_elements = host_list(all_elements, (_v) => host_tuple(_v, 3, [(_v2) => host_text(_v2), (_v2) => host_text(_v2), (_v2) => host_bool(_v2)]));
    const _r = exhaust_pairs_boundary(matches, all_elements);
    return _r.map((_v) => [text_str(_v[0]), text_str(_v[1]), _v[2], text_str(_v[3]), text_str(_v[4]), _v[5]]);
  }

  // trainer.src.mjs
  var BASE_ELEMENTS = /* @__PURE__ */ new Set(["Water", "Fire", "Wind", "Earth"]);
  var RATE_LIMIT = 60;
  var RATE_WINDOW = 6e4;
  var BULK_WARN = 200;
  var MAX_QUEUE_DEPTH = 50;
  var MAX_PERMUTATE_ROUNDS = 50;
  var history = [];
  var pairCache = /* @__PURE__ */ new Map();
  var cmdHistory = [];
  var cmdHistoryIdx = -1;
  var cancelled = false;
  var running = false;
  var waitingForConfirm = false;
  var confirmResolve = null;
  var commandQueue = [];
  var currentCommand = null;
  var activeAbort = null;
  var queueWorkerRunning = false;
  var output;
  var input;
  var body;
  var toggle;
  var stopBtn;
  var queueEl;
  function print(html) {
    const div2 = document.createElement("div");
    div2.innerHTML = html;
    output.appendChild(div2);
    output.scrollTop = output.scrollHeight;
  }
  function esc(s) {
    const d = document.createElement("span");
    d.textContent = s;
    return d.innerHTML;
  }
  function wrap(cls, html) {
    return `<span class="${cls}">${html}</span>`;
  }
  function bold(t) {
    return wrap("ict-bold", t);
  }
  function green(t) {
    return wrap("ict-green", t);
  }
  function yellow(t) {
    return wrap("ict-yellow", t);
  }
  function cyan(t) {
    return wrap("ict-cyan", t);
  }
  function magenta(t) {
    return wrap("ict-magenta", t);
  }
  function red(t) {
    return wrap("ict-red", t);
  }
  function dim(t) {
    return wrap("ict-dim", t);
  }
  function formatElement(el) {
    if (!el || !el.text) return dim("Nothing");
    let s = el.emoji ? `${el.emoji} ${esc(el.text)}` : esc(el.text);
    if (el.discovered) s += " " + magenta("[FIRST DISCOVERY!]");
    return s;
  }
  function formatResult(a, b, result) {
    return `  ${formatElement(a)} + ${formatElement(b)} = ${formatElement(result)}`;
  }
  var DB_NAME = "infinite-craft";
  var ITEMS_STORE = "items";
  var SAVES_STORE = "saves";
  var _items = [];
  var _allItems = [];
  var _nameIndex = {};
  var _idIndex = {};
  var _nextId = 0;
  var _saveId = 0;
  var _db = null;
  function openDB() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  function loadAllItems() {
    return new Promise((resolve, reject) => {
      const tx = _db.transaction(ITEMS_STORE, "readonly");
      const store = tx.objectStore(ITEMS_STORE);
      const req = store.getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
  }
  function loadSaves() {
    return new Promise((resolve, reject) => {
      const tx = _db.transaction(SAVES_STORE, "readonly");
      const store = tx.objectStore(SAVES_STORE);
      const req = store.getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
  }
  function detectActiveSave(saves, allItems) {
    let active = saves[0];
    for (const s of saves) {
      if (s.updated > active.updated) active = s;
    }
    return active ? active.id : 0;
  }
  function putItem(item) {
    if (!_db) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const tx = _db.transaction(ITEMS_STORE, "readwrite");
      const store = tx.objectStore(ITEMS_STORE);
      const req = store.put(item);
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }
  function deleteItem(id) {
    return new Promise((resolve, reject) => {
      const tx = _db.transaction(ITEMS_STORE, "readwrite");
      const store = tx.objectStore(ITEMS_STORE);
      const req = store.delete(id);
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }
  function rebuildIndexes() {
    _nameIndex = {};
    _idIndex = {};
    _nextId = 0;
    for (const item of _allItems) {
      if (item.id >= _nextId) _nextId = item.id + 1;
    }
    for (const item of _items) {
      _nameIndex[item.text] = item;
      _idIndex[item.id] = item;
    }
  }
  function getAllElements() {
    return _items;
  }
  function elementTuples() {
    return getAllElements().map((e) => [e.text, e.emoji || "", !!e.discovered]);
  }
  function resolveElement(name) {
    const [text, emoji, discovered] = resolve_element_boundary2(elementTuples(), name);
    return { text, emoji, discovered };
  }
  function addElement(text, emoji, discovered) {
    const existing = _nameIndex[text];
    if (existing) return false;
    const item = { id: _nextId++, saveId: _saveId, text, emoji: emoji || "" };
    if (discovered) item.discovered = true;
    _items.push(item);
    _nameIndex[text] = item;
    _idIndex[item.id] = item;
    putItem(item);
    return true;
  }
  async function removeElement(name) {
    const item = _nameIndex[name];
    if (!item || BASE_ELEMENTS.has(item.text)) return false;
    _items = _items.filter((i) => i.id !== item.id);
    _allItems = _allItems.filter((i) => i.id !== item.id);
    delete _nameIndex[item.text];
    delete _idIndex[item.id];
    await deleteItem(item.id);
    return true;
  }
  var recipeIndex = {};
  function rebuildRecipeIndex() {
    recipeIndex = {};
    for (const item of _items) {
      if (!item.recipes || !item.recipes.length) continue;
      for (const pair of item.recipes) {
        if (pair.length !== 2) continue;
        const a = _idIndex[pair[0]], b = _idIndex[pair[1]];
        if (!a || !b) continue;
        record_recipe2(recipeIndex, item.text, a.text, b.text);
      }
    }
  }
  function recordRecipe(resultName, aName, bName) {
    record_recipe2(recipeIndex, resultName, aName, bName);
    const resultItem = _nameIndex[resultName];
    const aItem = _nameIndex[aName];
    const bItem = _nameIndex[bName];
    if (resultItem && aItem && bItem) {
      if (!resultItem.recipes) resultItem.recipes = [];
      const pair = [aItem.id, bItem.id].sort((x, y) => x - y);
      const has = resultItem.recipes.some((r) => r[0] === pair[0] && r[1] === pair[1]);
      if (!has) {
        resultItem.recipes.push(pair);
        putItem(resultItem);
      }
    }
  }
  function _resetStateForParity(elements, recipes) {
    _items = elements.map(([text, emoji, discovered], i) => {
      const item = { id: i, saveId: 0, text, emoji: emoji || "" };
      if (discovered) item.discovered = true;
      return item;
    });
    _allItems = _items;
    _saveId = 0;
    rebuildIndexes();
    recipeIndex = {};
    for (const [result, pairs] of Object.entries(recipes || {})) {
      recipeIndex[result] = pairs.map(([a, b]) => [a, b]);
    }
  }
  function _getRecipeIndexForParity() {
    return recipeIndex;
  }
  var timestamps = [];
  function sleepCancellable(ms) {
    return new Promise((resolve, reject) => {
      const step = 50;
      let elapsed = 0;
      function tick() {
        if (cancelled) {
          reject(new Error("Cancelled"));
          return;
        }
        if (elapsed >= ms) {
          resolve();
          return;
        }
        const chunk = Math.min(step, ms - elapsed);
        elapsed += chunk;
        setTimeout(tick, chunk);
      }
      tick();
    });
  }
  function acquireRate() {
    return new Promise((resolve, reject) => {
      function tryAcquire() {
        if (cancelled) {
          reject(new Error("Cancelled"));
          return;
        }
        const now = Date.now();
        while (timestamps.length && timestamps[0] <= now - RATE_WINDOW) timestamps.shift();
        if (timestamps.length < RATE_LIMIT) {
          timestamps.push(now);
          resolve();
        } else {
          const wait = timestamps[0] + RATE_WINDOW - now + 10;
          sleepCancellable(wait).then(tryAcquire).catch(reject);
        }
      }
      tryAcquire();
    });
  }
  function pairKey(a, b) {
    const [ka, kb] = pair_key2(a, b);
    return ka + "\0" + kb;
  }
  async function apiPair(firstName, secondName) {
    if (cancelled) throw new Error("Cancelled");
    const key = pairKey(firstName, secondName);
    if (pairCache.has(key)) return pairCache.get(key);
    await acquireRate();
    if (cancelled) throw new Error("Cancelled");
    const url = `/api/infinite-craft/pair?first=${encodeURIComponent(firstName)}&second=${encodeURIComponent(secondName)}`;
    let resp;
    for (let attempt = 0; attempt < 3; attempt++) {
      if (cancelled) throw new Error("Cancelled");
      try {
        resp = await fetch(url, { signal: activeAbort ? activeAbort.signal : void 0 });
        if (resp.ok) break;
      } catch (e) {
      }
      if (attempt < 2) await sleepCancellable(1e3 * Math.pow(2, attempt));
    }
    if (cancelled) throw new Error("Cancelled");
    if (!resp || !resp.ok) throw new Error("API request failed");
    const json = await resp.json();
    let result = null;
    if (json.result && json.result !== "Nothing") {
      result = { text: json.result, emoji: json.emoji || "", discovered: !!json.isNew };
    }
    pairCache.set(key, result);
    return result;
  }
  async function doCombine(aName, bName) {
    const a = resolveElement(aName);
    const b = resolveElement(bName);
    try {
      beginRun();
      const result = await apiPair(a.text, b.text);
      if (cancelled) return;
      if (result) {
        addElement(a.text, a.emoji, false);
        addElement(b.text, b.emoji, false);
        const isNew = addElement(result.text, result.emoji, result.discovered);
        recordRecipe(result.text, a.text, b.text);
        history.push({ a: a.text, b: b.text, result: result.text });
        print(formatResult(a, b, result) + (isNew ? " " + green("(new)") : ""));
      } else {
        history.push({ a: a.text, b: b.text, result: "Nothing" });
        print(formatResult(a, b, null));
      }
    } catch (e) {
      if (!cancelled) print("  " + red(`Error: ${esc(e.message)}`));
    } finally {
      endRun();
    }
  }
  function matchElements(query) {
    const [rawMatches, error] = match_elements_boundary2(elementTuples(), query);
    const matches = rawMatches.map(([text, emoji, discovered]) => ({ text, emoji, discovered }));
    return { matches, error };
  }
  function beginRun() {
    cancelled = false;
    running = true;
    activeAbort = new AbortController();
    try {
      stopBtn.style.display = "inline";
    } catch {
    }
  }
  function endRun() {
    running = false;
    activeAbort = null;
    try {
      stopBtn.style.display = "none";
    } catch {
    }
  }
  async function runPairsInner(pairs) {
    let done = 0, newCount = 0, nothingCount = 0, errors = 0;
    const total = pairs.length;
    for (const [a, b] of pairs) {
      if (cancelled) {
        print("  " + yellow("Cancelled."));
        break;
      }
      try {
        const result = await apiPair(a.text, b.text);
        if (cancelled) {
          print("  " + yellow("Cancelled."));
          break;
        }
        done++;
        if (result) {
          const isNew = addElement(result.text, result.emoji, result.discovered);
          recordRecipe(result.text, a.text, b.text);
          history.push({ a: a.text, b: b.text, result: result.text });
          if (isNew) {
            newCount++;
            print(`  ${dim(`[${done}/${total}]`)} ${formatResult(a, b, result)} ${green("(new)")}`);
          }
        } else {
          nothingCount++;
          history.push({ a: a.text, b: b.text, result: "Nothing" });
        }
      } catch (e) {
        if (cancelled) {
          print("  " + yellow("Cancelled."));
          break;
        }
        done++;
        errors++;
      }
      await new Promise((r) => setTimeout(r, 0));
    }
    if (!cancelled) {
      print(`  Done: ${green(String(newCount))} new, ${dim(String(nothingCount))} nothing, ${errors ? red(String(errors)) + " errors" : "0 errors"} (${done}/${total})`);
    }
  }
  async function confirmAndRunPairs(pairs) {
    try {
      beginRun();
      if (pairs.length > BULK_WARN) {
        print(`  ${yellow(`${pairs.length} pairs`)} \u2014 type ${bold("y")} or ${bold("yes")} to continue, anything else to cancel.`);
        const answer = await waitForInput();
        if (cancelled || answer === "__cancelled__" || !["y", "yes"].includes(answer.toLowerCase())) {
          print("  Cancelled.");
          return;
        }
      }
      if (cancelled) return;
      print(`  Running ${bold(String(pairs.length))} combinations...`);
      await runPairsInner(pairs);
    } finally {
      endRun();
    }
  }
  function waitForInput() {
    return new Promise((resolve) => {
      waitingForConfirm = true;
      confirmResolve = resolve;
      function cleanup() {
        waitingForConfirm = false;
        confirmResolve = null;
        try {
          input.removeEventListener("keydown", handler, true);
        } catch {
        }
      }
      function handler(e) {
        if (e.key === "Enter") {
          const val = input.value.trim();
          if (is_local_command2(val)) return;
          e.stopImmediatePropagation();
          input.value = "";
          const answer = val.toLowerCase();
          if (answer === "y" || answer === "yes" || answer === "n" || answer === "no" || answer === "") {
            cleanup();
            resolve(val);
          } else {
            tryEnqueue(val);
          }
        }
      }
      try {
        input.addEventListener("keydown", handler, true);
      } catch (err) {
        cleanup();
        throw err;
      }
    });
  }
  function doSearch(query) {
    const { matches, error } = matchElements(query);
    if (error) {
      print("  " + red(error));
      return;
    }
    if (!matches.length) {
      print("  No matches found.");
      return;
    }
    for (const el of matches) print("  " + formatElement(el));
  }
  function doList() {
    const elements = getAllElements();
    if (!elements.length) {
      print("  No elements discovered.");
      return;
    }
    print(`  ${green(String(elements.length))} elements:`);
    for (const el of elements) print("  " + formatElement(el));
  }
  function traceRecipeCore(name) {
    const [status, target, steps] = trace_recipe_boundary2(elementTuples(), recipeIndex, name);
    return { status, target, steps };
  }
  function doRecipe(name) {
    const { status, target: targetName, steps } = traceRecipeCore(name);
    switch (status) {
      case 0:
        print("  " + red("Element not found."));
        return;
      case 1: {
        const el = resolveElement(targetName);
        print(`  ${formatElement(el)} is a base element.`);
        return;
      }
      case 2:
        print("  " + yellow("No recipe known. Try /import or /fill."));
        return;
      case 3:
        print("  " + yellow("Cannot trace full lineage \u2014 some intermediate recipes missing."));
        return;
      case 4: {
        const el = resolveElement(targetName);
        print(`  Recipe for ${formatElement(el)} (${bold(String(steps.length))} steps):`);
        for (let i = 0; i < steps.length; i++) {
          const [a, b, result] = steps[i];
          const aEl = resolveElement(a);
          const bEl = resolveElement(b);
          const rEl = resolveElement(result);
          print(`  ${dim(String(i + 1) + ".")} ${formatElement(aEl)} + ${formatElement(bEl)} = ${formatElement(rEl)}`);
        }
        return;
      }
    }
  }
  async function doExhaust(query) {
    const { matches, error } = matchElements(query);
    if (error) {
      print("  " + red(error));
      return;
    }
    if (!matches.length) {
      print(`  No elements match: ${esc(query)}`);
      return;
    }
    const rawPairs = exhaust_pairs_boundary2(
      matches.map((e) => [e.text, e.emoji || "", !!e.discovered]),
      elementTuples()
    );
    const pairs = rawPairs.map(([at2, ae, af, bt, be, bf]) => [
      { text: at2, emoji: ae, discovered: af },
      { text: bt, emoji: be, discovered: bf }
    ]);
    if (!pairs.length) {
      print(`  No valid pairs for query: ${esc(query)}`);
      return;
    }
    print(`  Exhausting ${matches.length} element(s) matching ${yellow(esc(query))} with all discoveries (${pairs.length} pairs)...`);
    if (matches.length <= 10) {
      for (const m of matches) print(`    ${formatElement(m)}`);
    }
    await confirmAndRunPairs(pairs);
  }
  async function doCrawl(aName, bName) {
    const a = resolveElement(aName);
    const b = resolveElement(bName);
    print(`  Crawling from ${formatElement(a)} + ${formatElement(b)}...`);
    try {
      beginRun();
      let pool = /* @__PURE__ */ new Set();
      const tried = /* @__PURE__ */ new Set();
      const result = await apiPair(a.text, b.text);
      if (cancelled) {
        print("  " + yellow("Crawl cancelled."));
        return;
      }
      tried.add(pairKey(a.text, b.text));
      if (result) {
        addElement(a.text, a.emoji, false);
        addElement(b.text, b.emoji, false);
        pool.add(a.text);
        pool.add(b.text);
        const isNew = addElement(result.text, result.emoji, result.discovered);
        recordRecipe(result.text, a.text, b.text);
        history.push({ a: a.text, b: b.text, result: result.text });
        pool.add(result.text);
        print(`  ${formatResult(a, b, result)}${isNew ? " " + green("(new)") : ""}`);
      } else {
        print(formatResult(a, b, null));
        return;
      }
      let gen = 1;
      while (!cancelled) {
        const elements = [...pool].map((n) => resolveElement(n));
        const pairs = [];
        for (let i = 0; i < elements.length; i++) {
          for (let j = i; j < elements.length; j++) {
            const key = pairKey(elements[i].text, elements[j].text);
            if (!tried.has(key)) {
              pairs.push([elements[i], elements[j]]);
              tried.add(key);
            }
          }
        }
        if (!pairs.length) {
          print("  " + dim("No more untried pairs."));
          break;
        }
        print(`  ${dim(`Gen ${gen}:`)} ${pairs.length} pairs to try...`);
        let newInGen = 0;
        for (const [pa, pb] of pairs) {
          if (cancelled) break;
          try {
            const r = await apiPair(pa.text, pb.text);
            if (r) {
              const isNew = addElement(r.text, r.emoji, r.discovered);
              recordRecipe(r.text, pa.text, pb.text);
              history.push({ a: pa.text, b: pb.text, result: r.text });
              if (isNew && !pool.has(r.text)) {
                pool.add(r.text);
                newInGen++;
                print(`  ${formatResult(pa, pb, r)} ${green("(new)")}`);
              }
            }
          } catch (e) {
            if (cancelled) break;
          }
          await new Promise((r) => setTimeout(r, 0));
        }
        print(`  ${dim(`Gen ${gen} done:`)} ${green(String(newInGen))} new elements.`);
        if (newInGen === 0) break;
        gen++;
      }
      if (cancelled) print("  " + yellow("Crawl cancelled."));
      else print(`  Pool size: ${bold(String(pool.size))} elements.`);
    } finally {
      endRun();
    }
  }
  async function doPermute(query) {
    const { matches, error } = matchElements(query);
    if (error) {
      print("  " + red(error));
      return;
    }
    if (!matches.length) {
      print("  No elements match that query.");
      return;
    }
    if (matches.length === 1) {
      print(`  Only one match: ${formatElement(matches[0])}. Need at least two.`);
      return;
    }
    const pairs = [];
    for (let i = 0; i < matches.length; i++) {
      for (let j = i + 1; j < matches.length; j++) {
        pairs.push([matches[i], matches[j]]);
      }
    }
    print(`  ${matches.length} elements match, ${pairs.length} unique pairs:`);
    for (const m of matches) print(`    ${formatElement(m)}`);
    await confirmAndRunPairs(pairs);
  }
  async function doPermutate(query) {
    let round = 0;
    let confirmed = false;
    let stopped = false;
    print(`  Permutating matches for ${yellow(esc(query))} until no new discoveries...`);
    try {
      beginRun();
      while (true) {
        if (cancelled) {
          stopped = true;
          break;
        }
        if (round >= MAX_PERMUTATE_ROUNDS) {
          print(`  Reached max rounds (${MAX_PERMUTATE_ROUNDS}). Stopping.`);
          break;
        }
        round++;
        const knownBefore = new Set(getAllElements().map((e) => e.text));
        const { matches, error } = matchElements(query);
        if (error) {
          print("  " + red(error));
          return;
        }
        if (!matches.length) {
          print("  No elements match that query.");
          return;
        }
        if (matches.length === 1) {
          print(`  Only one match: ${formatElement(matches[0])}. Need at least two.`);
          return;
        }
        const pairs = [];
        for (let i = 0; i < matches.length; i++) {
          for (let j = i + 1; j < matches.length; j++) {
            pairs.push([matches[i], matches[j]]);
          }
        }
        print(`  ${dim(`--- Round ${round}:`)} ${matches.length} elements, ${pairs.length} pairs ---`);
        if (!confirmed && pairs.length > BULK_WARN) {
          print(`  ${yellow(`${pairs.length} pairs per round`)} \u2014 type ${bold("y")} or ${bold("yes")} to continue.`);
          const answer = await waitForInput();
          if (cancelled || answer === "__cancelled__" || !["y", "yes"].includes(answer.toLowerCase())) {
            print("  Cancelled.");
            return;
          }
          confirmed = true;
        }
        await runPairsInner(pairs);
        if (cancelled) {
          stopped = true;
          break;
        }
        const knownAfter = new Set(getAllElements().map((e) => e.text));
        let newCount = 0;
        for (const name of knownAfter) {
          if (!knownBefore.has(name)) newCount++;
        }
        print(`  +${newCount} new elements`);
        if (newCount === 0) {
          print("  No new discoveries. Stopping.");
          break;
        }
      }
      if (stopped) print("  " + yellow("Stopped."));
      else print(`  Permutate done after ${round} round(s).`);
    } finally {
      endRun();
    }
  }
  async function doCross(leftQ, rightQ) {
    const leftResult = matchElements(leftQ);
    if (leftResult.error) {
      print("  " + red(leftResult.error));
      return;
    }
    const rightResult = matchElements(rightQ);
    if (rightResult.error) {
      print("  " + red(rightResult.error));
      return;
    }
    const left = leftResult.matches;
    const right = rightResult.matches;
    if (!left.length) {
      print(`  No elements match: ${esc(leftQ)}`);
      return;
    }
    if (!right.length) {
      print(`  No elements match: ${esc(rightQ)}`);
      return;
    }
    const seen = /* @__PURE__ */ new Set();
    const pairs = [];
    for (const a of left) {
      for (const b of right) {
        if (a.text === b.text) continue;
        const key = pairKey(a.text, b.text);
        if (seen.has(key)) continue;
        seen.add(key);
        pairs.push([a, b]);
      }
    }
    if (!pairs.length) {
      print("  No valid pairs (all matches overlap).");
      return;
    }
    const leftPreview = left.slice(0, 10).map((e) => e.text).join(", ");
    const rightPreview = right.slice(0, 10).map((e) => e.text).join(", ");
    print(`  Left (${left.length}): ${esc(leftPreview)}${left.length > 10 ? "..." : ""}`);
    print(`  Right (${right.length}): ${esc(rightPreview)}${right.length > 10 ? "..." : ""}`);
    print(`  ${pairs.length} unique pairs`);
    await confirmAndRunPairs(pairs);
  }
  async function doCombineWithQuery(name, query) {
    const target = resolveElement(name);
    const { matches: others, error } = matchElements(query);
    if (error) {
      print("  " + red(error));
      return;
    }
    if (!others.length) {
      print(`  No elements match: ${esc(query)}`);
      return;
    }
    const pairs = others.filter((o) => o.text !== target.text).map((o) => [target, o]);
    if (!pairs.length) {
      print(`  No other elements match: ${esc(query)}`);
      return;
    }
    print(`  Combining ${bold(esc(target.text))} with ${pairs.length} elements matching ${yellow(esc(query))}...`);
    await confirmAndRunPairs(pairs);
  }
  async function fetchRetry(url, maxRetries = 3) {
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      if (cancelled) throw new Error("Cancelled");
      const resp = await fetch(url, { signal: activeAbort ? activeAbort.signal : void 0 });
      if (resp.ok) return resp;
      if (resp.status === 429 && attempt < maxRetries) {
        const wait = Math.pow(2, attempt + 1) * 1e3;
        await sleepCancellable(wait);
        continue;
      }
      return resp;
    }
  }
  function processRecipeSteps(steps) {
    let count = 0;
    for (const step of steps) {
      const aText = step.a?.id || step.a?.text;
      const bText = step.b?.id || step.b?.text;
      const rText = step.result?.id || step.result?.text;
      const aEmoji = step.a?.emoji || "";
      const bEmoji = step.b?.emoji || "";
      const rEmoji = step.result?.emoji || "";
      if (aText) addElement(aText, aEmoji, false);
      if (bText) addElement(bText, bEmoji, false);
      if (rText) {
        addElement(rText, rEmoji, false);
        if (aText && bText) recordRecipe(rText, aText, bText);
        count++;
      }
    }
    return count;
  }
  function pickFile(accept) {
    return new Promise((resolve) => {
      const input2 = document.createElement("input");
      input2.type = "file";
      if (accept) input2.accept = accept;
      input2.onchange = () => resolve(input2.files[0] || null);
      input2.click();
    });
  }
  async function doImportFile() {
    try {
      beginRun();
      print("  Select a .ic save file...");
      const file = await pickFile(".ic");
      if (!file || cancelled) {
        print("  " + yellow("Cancelled."));
        return;
      }
      print(`  Reading ${bold(esc(file.name))}...`);
      const arrayBuf = await file.arrayBuffer();
      let json;
      try {
        const stream = new Blob([arrayBuf]).stream().pipeThrough(new DecompressionStream("gzip"));
        const text = await new Response(stream).text();
        json = JSON.parse(text);
      } catch {
        const text = new TextDecoder().decode(arrayBuf);
        json = JSON.parse(text);
      }
      const items = json.items || [];
      if (!items.length) {
        print("  No items in save file.");
        return;
      }
      const idToItem = {};
      for (const item of items) idToItem[item.id] = item;
      let importedCount = 0, recipeCount = 0;
      for (const item of items) {
        if (cancelled) break;
        const text = item.text;
        const emoji = item.emoji || "";
        const discovered = !!(item.discovery || item.discovered);
        const isNew = addElement(text, emoji, discovered);
        if (isNew) importedCount++;
        if (item.recipes) {
          for (const pair of item.recipes) {
            if (pair.length === 2 && idToItem[pair[0]] && idToItem[pair[1]]) {
              recordRecipe(text, idToItem[pair[0]].text, idToItem[pair[1]].text);
              recipeCount++;
            }
          }
        }
      }
      rebuildRecipeIndex();
      if (cancelled) print("  " + yellow("Import cancelled."));
      else print(`  Loaded ${green(String(items.length))} elements (${importedCount} new) with ${recipeCount} recipes from ${bold(esc(file.name))}.`);
    } catch (e) {
      if (!cancelled) print("  " + red(`Error reading save file: ${esc(e.message)}`));
    } finally {
      endRun();
    }
  }
  async function doImport(name) {
    try {
      beginRun();
      print(`  Importing ${bold(esc(name))} from Infinibrowser...`);
      const itemResp = await fetchRetry(`https://infinibrowser.wiki/api/item?id=${encodeURIComponent(name)}`);
      if (cancelled) return;
      if (!itemResp.ok) {
        print("  " + red("Element not found on Infinibrowser."));
        return;
      }
      const recipeResp = await fetchRetry(`https://infinibrowser.wiki/api/recipe?id=${encodeURIComponent(name)}`);
      if (cancelled) return;
      if (!recipeResp.ok) {
        print("  " + red("No recipe found on Infinibrowser."));
        return;
      }
      const recipeData = await recipeResp.json();
      const steps = recipeData.steps || recipeData.recipe || [];
      if (!steps.length) {
        print("  " + yellow("No recipe steps found."));
        return;
      }
      const count = processRecipeSteps(steps);
      rebuildRecipeIndex();
      print(`  Imported ${green(String(count))} recipe steps for ${bold(esc(name))}.`);
    } catch (e) {
      if (!cancelled) print("  " + red(`Import failed: ${esc(e.message)}. CORS may be blocked \u2014 try the Python CLI instead.`));
    } finally {
      endRun();
    }
  }
  async function doFill() {
    const elements = getAllElements();
    const missing = elements.filter((e) => !BASE_ELEMENTS.has(e.text) && (!recipeIndex[e.text] || !recipeIndex[e.text].length));
    if (!missing.length) {
      print("  All elements have recipes.");
      return;
    }
    print(`  ${yellow(String(missing.length))} elements missing recipes. Fetching from Infinibrowser...`);
    let filled2 = 0, errors = 0;
    try {
      beginRun();
      for (let i = 0; i < missing.length; i++) {
        if (cancelled) {
          print("  " + yellow("Fill cancelled."));
          break;
        }
        const el = missing[i];
        try {
          const recipeResp = await fetchRetry(`https://infinibrowser.wiki/api/recipe?id=${encodeURIComponent(el.text)}`);
          if (recipeResp.ok) {
            const data = await recipeResp.json();
            const steps = data.steps || data.recipe || [];
            processRecipeSteps(steps);
            filled2++;
          } else {
            errors++;
          }
        } catch {
          errors++;
        }
        if ((i + 1) % 10 === 0 || i === missing.length - 1) {
          print(`  ${dim(`[${i + 1}/${missing.length}]`)} ${green(String(filled2))} filled, ${errors ? red(String(errors)) + " failed" : "0 failed"}`);
        }
        await sleepCancellable(500);
      }
    } finally {
      rebuildRecipeIndex();
      endRun();
    }
    print(`  Done: ${green(String(filled2))} filled, ${errors ? red(String(errors)) + " failed" : "0 failed"} (${missing.length} total).`);
  }
  function doUnfilled() {
    const elements = getAllElements();
    const missing = elements.filter((e) => !BASE_ELEMENTS.has(e.text) && (!recipeIndex[e.text] || !recipeIndex[e.text].length));
    if (!missing.length) {
      print("  All elements have recipes.");
      return;
    }
    print(`  ${yellow(String(missing.length))} elements without recipes:`);
    for (const el of missing) print("  " + formatElement(el));
  }
  function findOrphanCandidates() {
    return orphan_candidates_boundary2(elementTuples(), recipeIndex).map(
      ([text, emoji, discovered]) => ({ text, emoji, discovered })
    );
  }
  async function ibCanFill(name) {
    try {
      const itemResp = await fetchRetry(`https://infinibrowser.wiki/api/item?id=${encodeURIComponent(name)}`);
      if (itemResp.status === 404) return false;
      if (!itemResp.ok) return null;
      const itemData = await itemResp.json();
      if (itemData.code) return false;
      const recipeResp = await fetchRetry(`https://infinibrowser.wiki/api/recipe?id=${encodeURIComponent(name)}`);
      if (recipeResp.status === 404) return false;
      if (!recipeResp.ok) return null;
      const recipeData = await recipeResp.json();
      if (recipeData.code) return false;
      const steps = recipeData.steps || recipeData.recipe || [];
      return steps.length > 0;
    } catch {
      return null;
    }
  }
  async function doPrune() {
    const candidates = findOrphanCandidates();
    if (!candidates.length) {
      print("  Nothing to prune.");
      return;
    }
    print(`  ${yellow(String(candidates.length))} orphan element${candidates.length === 1 ? "" : "s"} to check on Infinibrowser...`);
    let pruned = 0, kept = 0, skipped = 0;
    try {
      beginRun();
      for (let i = 0; i < candidates.length; i++) {
        if (cancelled) {
          print("  " + yellow("Prune cancelled."));
          break;
        }
        const el = candidates[i];
        const fillable = await ibCanFill(el.text);
        if (fillable === null) {
          skipped++;
        } else if (fillable) {
          kept++;
        } else {
          await removeElement(el.text);
          pruned++;
        }
        if ((i + 1) % 10 === 0 || i === candidates.length - 1) {
          print(`  ${dim(`[${i + 1}/${candidates.length}]`)} ${green(String(pruned))} pruned, ${kept} kept, ${skipped ? yellow(String(skipped)) + " skipped" : "0 skipped"}`);
        }
        await sleepCancellable(500);
      }
    } finally {
      rebuildIndexes();
      rebuildRecipeIndex();
      endRun();
    }
    print(`  Done: ${green(String(pruned))} pruned, ${kept} fillable on Infinibrowser (kept), ${skipped ? yellow(String(skipped)) + " skipped (API errors)" : "0 skipped"}.`);
  }
  function exportIncludedCore() {
    return export_elements_boundary2(elementTuples(), recipeIndex);
  }
  async function doExport() {
    const included = exportIncludedCore();
    const includedNames = new Set(included.map((t) => t[0]));
    const exportItems = _items.filter((item) => includedNames.has(item.text)).map((item) => {
      const exportItem = { id: item.id, text: item.text, emoji: item.emoji || "" };
      if (item.discovered) exportItem.discovery = true;
      if (item.recipes && item.recipes.length) exportItem.recipes = item.recipes;
      return exportItem;
    });
    const now = Date.now();
    const save = { name: "Trainer Export", version: "1.0", created: now, updated: now, instances: [], items: exportItems };
    const json = JSON.stringify(save);
    const stream = new Blob([json]).stream().pipeThrough(new CompressionStream("gzip"));
    const gzipped = await new Response(stream).blob();
    const url = URL.createObjectURL(gzipped);
    const a = document.createElement("a");
    a.href = url;
    a.download = "infinite-craft-export.ic";
    a.click();
    URL.revokeObjectURL(url);
    print(`  Exported ${green(String(exportItems.length))} elements (gzip compressed).`);
  }
  function doHistory() {
    if (!history.length) {
      print("  No combinations this session.");
      return;
    }
    print(`  ${bold(String(history.length))} combinations:`);
    for (const h of history) {
      print(`  ${esc(h.a)} + ${esc(h.b)} = ${esc(h.result)}`);
    }
  }
  function doHelp() {
    print(`  ${bold("Combine:")}
    ${cyan("<element> + <element>")}       Combine two elements
    ${cyan("/combine <element> <element>")}  Combine two elements

  ${bold("Crawl:")}
    ${cyan("<element> ++ <element>")}      Combine & crawl until no new discoveries
    ${cyan("/crawl <element> <element>")}  Combine & crawl until no new discoveries

  ${bold("Bulk combine (query syntax below):")}
    ${cyan("<element> +| <query>")}        Combine element with all matching discoveries
    ${cyan("/with <element> <query>")}     Combine element with all matching discoveries
    ${cyan("<query> * <query>")}           Cross-combine matches from both queries
    ${cyan("/cross <query> <query>")}    Cross-combine matches from both queries
    ${cyan("/permute <query>")}            Combine all matching elements with each other
    ${cyan("/permutate <query>")}          Permute repeatedly until no new discoveries
    ${cyan("/exhaust <query>")}            Each match combined with all discoveries

  ${bold("Query syntax (/search, /with, /permute, /permutate, /cross, /exhaust, shorthands):")}
    substring                   Default: case-insensitive substring
    * ? []                      fnmatch wildcards (e.g. fire*, mu?)
    /pattern/                   Regex, case-insensitive (no | alternation)
    !<query>                    Exclude matches (e.g. !fire* = everything except fire*)
    !                           All elements (exclude nothing)
    ^<query>                    First discoveries only (e.g. ^fire* = new fire* matches)
    ^                           All first discoveries

  ${bold("Discoveries & recipes:")}
    ${cyan("/search <query>")}             Search discoveries
    ${cyan("/recipe <element>")}           Show shortest recipe from base elements
    ${cyan("/list")}                       List all discovered elements
    ${cyan("/import <element|file.ic>")}   Import from Infinibrowser or .ic save file
    ${cyan("/fill")}                       Fetch missing recipes from Infinibrowser
    ${cyan("/unfilled")}                   List elements without recipes
    ${cyan("/prune")}                      Remove orphan elements Infinibrowser can't fill
    ${cyan("/export")}                     Download discoveries as .ic save file
    ${cyan("/history")}                    Show combinations this session
    ${cyan("/clear")}                      Clear output (browser only)
    ${cyan("/help")}                       Show this help`);
  }
  function updateQueueDisplay() {
    if (!currentCommand && !commandQueue.length) {
      queueEl.style.display = "none";
      queueEl.innerHTML = "";
      return;
    }
    queueEl.style.display = "block";
    let html = "";
    if (currentCommand) {
      html += `<div class="ict-queue-running">Running: ${esc(currentCommand)}</div>`;
    }
    if (commandQueue.length) {
      html += `<div class="ict-queue-label">Queue:</div>`;
      for (const cmd of commandQueue) {
        html += `<div class="ict-queue-item">${esc(cmd)}</div>`;
      }
    }
    queueEl.innerHTML = html;
  }
  function enqueueCommand(line) {
    const deferred = queueWorkerRunning || currentCommand !== null || waitingForConfirm;
    commandQueue.push(line);
    updateQueueDisplay();
    if (deferred) print("  " + dim(`Queued: ${esc(line)}`));
    ensureQueueWorker();
  }
  function tryEnqueue(line) {
    const error = validate_command_line2(line);
    if (error) {
      print(error);
      return false;
    }
    if (line === currentCommand || commandQueue.includes(line)) {
      print("  " + dim("Already queued."));
      return false;
    }
    if (commandQueue.length >= MAX_QUEUE_DEPTH) {
      print("  " + yellow(`Queue full (max ${MAX_QUEUE_DEPTH}).`));
      return false;
    }
    enqueueCommand(line);
    return true;
  }
  async function ensureQueueWorker() {
    if (queueWorkerRunning) return;
    queueWorkerRunning = true;
    try {
      while (commandQueue.length) {
        const line = commandQueue.shift();
        updateQueueDisplay();
        currentCommand = line;
        updateQueueDisplay();
        cancelled = false;
        try {
          await executeCommand(line);
        } catch (err) {
          endRun();
          waitingForConfirm = false;
          confirmResolve = null;
          print("  " + red("Error: " + esc(err && err.message || String(err))));
        }
        currentCommand = null;
        updateQueueDisplay();
      }
    } finally {
      queueWorkerRunning = false;
    }
  }
  async function executeClassified(kind, payload, line) {
    if (kind === "permute") {
      if (!payload.trim()) print("  Usage: /permute <query>");
      else await doPermute(payload.trim());
      return;
    }
    if (kind === "permutate") {
      if (!payload.trim()) print("  Usage: /permutate <query>");
      else await doPermutate(payload.trim());
      return;
    }
    if (kind === "import") {
      if (!payload.trim()) await doImportFile();
      else if (payload.endsWith(".ic") || payload.includes("/") || payload.includes("\\")) await doImportFile();
      else await doImport(payload.trim());
      return;
    }
    if (kind === "fill") {
      await doFill();
      return;
    }
    if (kind === "prune") {
      await doPrune();
      return;
    }
    if (kind === "export") {
      await doExport();
      return;
    }
    if (kind === "exhaust") {
      if (!payload.trim()) print("  Usage: /exhaust <query>");
      else await doExhaust(payload.trim());
      return;
    }
    if (kind === "combine" || kind === "crawl") {
      const pipeErr = slash_combine_crawl_pipe_error2(payload);
      if (pipeErr) {
        print(pipeErr);
        return;
      }
      const opErr = slash_combine_crawl_operator_error2(payload, kind);
      if (opErr) {
        print(opErr);
        return;
      }
      const parsed = parse_two_elements2(payload);
      if (!parsed) print(`  Usage: /${kind} <element> <element>`);
      else if (kind === "combine") await doCombine(parsed[0], parsed[1]);
      else await doCrawl(parsed[0], parsed[1]);
      return;
    }
    if (kind === "with") {
      const parsed = parse_with_args2(payload);
      if (!parsed) print("  Usage: /with <element> <query>");
      else await doCombineWithQuery(parsed[0], parsed[1]);
      return;
    }
    if (kind === "cross") {
      const opErr = slash_cross_operator_error2(payload);
      if (opErr) {
        print(opErr);
        return;
      }
      const parsed = parse_cross_queries2(payload);
      if (!parsed) print("  Usage: /cross <query> <query>");
      else await doCross(parsed[0], parsed[1]);
      return;
    }
    if (kind === "++") {
      const [a, b] = line.split(" ++ ", 2).map((s) => s.trim());
      if (a && b) await doCrawl(a, b);
      else print("  Usage: <element> ++ <element>");
      return;
    }
    if (kind === "bad+|") {
      print(`  Use <element> +| <query> (no space between + and |). Type ${yellow("/help")} for commands.`);
      return;
    }
    if (kind === "+|") {
      const parts = line.split("+|", 2);
      const name = parts[0].trim();
      const query = parts[1].trim();
      if (name && query) await doCombineWithQuery(name, query);
      else print("  Usage: <element> +| <query>");
      return;
    }
    if (kind === "*") {
      const [left, right] = line.split(" * ", 2).map((s) => s.trim());
      if (left && right) await doCross(left, right);
      else print("  Usage: <query> * <query>");
      return;
    }
    if (kind === "+") {
      const parts = line.includes(" + ") ? line.split(" + ", 2).map((s) => s.trim()) : [line.trimEnd().replace(/ \+$/, "").trim(), ""];
      if (parts[0] && parts[1]) await doCombine(parts[0], parts[1]);
      else print("  Usage: <element> + <element>");
    }
  }
  async function executeCommand(line) {
    let rest;
    if ((rest = slash_args2(line, "/help")) !== null) {
      doHelp();
      return;
    }
    if ((rest = slash_args2(line, "/search")) !== null) {
      if (!rest) print("  Usage: /search <query>");
      else doSearch(rest);
      return;
    }
    if ((rest = slash_args2(line, "/recipe")) !== null) {
      if (!rest) print("  Usage: /recipe <element>");
      else doRecipe(rest);
      return;
    }
    if ((rest = slash_args2(line, "/list")) !== null) {
      doList();
      return;
    }
    if ((rest = slash_args2(line, "/history")) !== null) {
      doHistory();
      return;
    }
    if ((rest = slash_args2(line, "/clear")) !== null) {
      output.innerHTML = "";
      return;
    }
    if ((rest = slash_args2(line, "/unfilled")) !== null) {
      doUnfilled();
      return;
    }
    const classified = classify_command_line2(line);
    if (!classified) {
      const error = validate_command_line2(line);
      print(error);
      return;
    }
    await executeClassified(classified[0], classified[1], line);
  }
  async function dispatch(line) {
    if (is_local_command2(line)) {
      await executeCommand(line);
      return;
    }
    tryEnqueue(line);
  }
  function initBrowserUI() {
    if (window.__ICTrainer) return;
    const style = document.createElement("style");
    style.textContent = `
    #ict-container{position:fixed;bottom:0;left:0;right:0;z-index:999999;font-family:'Menlo','Consolas','Monaco',monospace;font-size:13px;line-height:1.4}
    #ict-header{background:#0f3460;color:#e0e0e0;padding:4px 10px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;user-select:none}
    #ict-header span{font-weight:bold}
    #ict-body{background:#1a1a2e;color:#e0e0e0;display:flex;flex-direction:column}
    #ict-output{overflow-y:auto;max-height:300px;padding:6px 10px;white-space:pre-wrap;word-break:break-word}
    #ict-output div{margin:1px 0}
    #ict-queue{display:none;border-top:1px solid #0f3460;padding:4px 10px;background:#12182b;font-size:12px;max-height:80px;overflow-y:auto}
    #ict-queue .ict-queue-label{color:#ffeb3b;margin-bottom:2px}
    #ict-queue .ict-queue-item{margin:1px 0;opacity:.85}
    #ict-queue .ict-queue-running{color:#ffeb3b;margin-bottom:4px}
    #ict-input-row{display:flex;align-items:center;border-top:1px solid #0f3460;padding:4px 10px;background:#16213e}
    #ict-prompt{color:#00bcd4;margin-right:6px;white-space:nowrap}
    #ict-input{flex:1;background:transparent;border:none;outline:none;color:#e0e0e0;font:inherit;caret-color:#00bcd4}
    #ict-stop{display:none;background:#e53935;color:#fff;border:none;padding:2px 8px;margin-left:6px;cursor:pointer;font:inherit;border-radius:3px}
    .ict-green{color:#4caf50}.ict-magenta{color:#e040fb}.ict-dim{opacity:.5}.ict-bold{font-weight:bold}
    .ict-yellow{color:#ffeb3b}.ict-cyan{color:#00bcd4}.ict-red{color:#f44336}
  `;
    document.head.appendChild(style);
    const container = document.createElement("div");
    container.id = "ict-container";
    container.innerHTML = `
    <div id="ict-header"><span>\u26A1 Infinite Craft Trainer</span><button id="ict-toggle" style="background:none;border:none;color:#e0e0e0;cursor:pointer;font-size:16px">\u25BC</button></div>
    <div id="ict-body">
      <div id="ict-output"></div>
      <div id="ict-queue"></div>
      <div id="ict-input-row">
        <span id="ict-prompt">craft&gt;</span>
        <input id="ict-input" autocomplete="off" spellcheck="false" placeholder="Type /help for commands">
        <button id="ict-stop">Stop</button>
      </div>
    </div>`;
    document.body.appendChild(container);
    window.__ICTrainer = true;
    document.dispatchEvent(new CustomEvent("ict-trainer-ready"));
    output = document.getElementById("ict-output");
    queueEl = document.getElementById("ict-queue");
    input = document.getElementById("ict-input");
    body = document.getElementById("ict-body");
    toggle = document.getElementById("ict-toggle");
    stopBtn = document.getElementById("ict-stop");
    let collapsed = false;
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      collapsed = !collapsed;
      body.style.display = collapsed ? "none" : "flex";
      toggle.textContent = collapsed ? "\u25B2" : "\u25BC";
    });
    document.getElementById("ict-header").addEventListener("click", () => {
      toggle.click();
    });
    stopBtn.addEventListener("click", () => {
      cancelled = true;
      if (activeAbort) activeAbort.abort();
      if (waitingForConfirm && confirmResolve) {
        waitingForConfirm = false;
        const resolve = confirmResolve;
        confirmResolve = null;
        resolve("__cancelled__");
      }
    });
    function handleTrainerWheel(e) {
      if (collapsed || body.style.display === "none") return;
      output.scrollTop += e.deltaY;
      e.preventDefault();
      e.stopPropagation();
    }
    container.addEventListener("wheel", handleTrainerWheel, { passive: false });
    input.focus();
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const line = input.value.trim();
        if (!line) return;
        if (waitingForConfirm && !is_local_command2(line)) return;
        input.value = "";
        cmdHistory.push(line);
        cmdHistoryIdx = cmdHistory.length;
        print(cyan("craft&gt;") + " " + esc(line));
        dispatch(line).catch((err) => {
          endRun();
          waitingForConfirm = false;
          confirmResolve = null;
          currentCommand = null;
          updateQueueDisplay();
          print("  " + red("Error: " + esc(err && err.message || String(err))));
        });
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (cmdHistoryIdx > 0) {
          cmdHistoryIdx--;
          input.value = cmdHistory[cmdHistoryIdx];
        }
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        if (cmdHistoryIdx < cmdHistory.length - 1) {
          cmdHistoryIdx++;
          input.value = cmdHistory[cmdHistoryIdx];
        } else {
          cmdHistoryIdx = cmdHistory.length;
          input.value = "";
        }
      }
    });
    print(dim("Loading game data..."));
    openDB().then((db) => {
      _db = db;
      return Promise.all([loadAllItems(), loadSaves()]);
    }).then(([allItems, saves]) => {
      _allItems = allItems;
      _saveId = detectActiveSave(saves, allItems);
      _items = allItems.filter((i) => i.saveId === _saveId);
      const saveName = (saves.find((s) => s.id === _saveId) || {}).name || `Save ${_saveId}`;
      rebuildIndexes();
      rebuildRecipeIndex();
      output.innerHTML = "";
      print(bold(cyan("=== Infinite Craft Trainer ===")));
      print(`  Active save: ${bold(esc(saveName))} (id=${_saveId})`);
      print(`  ${green(String(_items.length))} elements loaded.`);
      const withRecipes = _items.filter((i) => i.recipes && i.recipes.length).length;
      print(`  ${green(String(withRecipes))} recipes known.`);
      print(`  Type ${yellow("/help")} for commands.`);
      print("");
    }).catch((err) => {
      print(red("Failed to load game data: " + esc(err.message)));
    });
  }
  var isBrowser = typeof window !== "undefined" && typeof document !== "undefined";
  if (isBrowser) {
    initBrowserUI();
  }
})();
