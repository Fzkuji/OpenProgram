// Expression evaluator for the calculator. No eval(): the application CSP
// forbids it, and a small recursive-descent parser gives clear errors.
//
// Grammar (lowest to highest precedence):
//   sum      := product (("+" | "-") product)*      a + b% means a + a*b/100
//   product  := unary (("*" | "/" | implicit) unary)*
//   unary    := ("-" | "+") unary | power
//   power    := postfix ("^" unary)?                 right associative
//   postfix  := primary ("!" | "%" | "²")*
//   primary  := number | constant | function "(" sum ")" | "√" postfix | "(" sum ")"

export class CalcError extends Error {}

const FUNCTIONS = {
  sin: (x, deg) => Math.sin(deg ? (x * Math.PI) / 180 : x),
  cos: (x, deg) => Math.cos(deg ? (x * Math.PI) / 180 : x),
  tan: (x, deg) => {
    const r = deg ? (x * Math.PI) / 180 : x;
    if (deg && Math.abs(((x % 180) + 180) % 180 - 90) < 1e-12) throw new CalcError("tan is undefined here");
    return Math.tan(r);
  },
  asin: (x, deg) => checkDomain(Math.asin(x), deg, "asin needs a value from −1 to 1"),
  acos: (x, deg) => checkDomain(Math.acos(x), deg, "acos needs a value from −1 to 1"),
  atan: (x, deg) => (deg ? (Math.atan(x) * 180) / Math.PI : Math.atan(x)),
  ln: x => { if (x <= 0) throw new CalcError("ln needs a positive number"); return Math.log(x); },
  log: x => { if (x <= 0) throw new CalcError("log needs a positive number"); return Math.log10(x); },
  sqrt: x => { if (x < 0) throw new CalcError("√ needs a non-negative number"); return Math.sqrt(x); },
  abs: x => Math.abs(x),
  exp: x => Math.exp(x),
};
const CONSTANTS = { pi: Math.PI, "π": Math.PI, e: Math.E };

function checkDomain(value, deg, message) {
  if (Number.isNaN(value)) throw new CalcError(message);
  return deg ? (value * 180) / Math.PI : value;
}

function factorial(n) {
  if (n < 0 || !Number.isInteger(n)) throw new CalcError("n! needs a whole number ≥ 0");
  if (n > 170) return Infinity;
  let r = 1;
  for (let i = 2; i <= n; i++) r *= i;
  return r;
}

export function tokenize(source) {
  const text = source
    .replace(/[×✕·]/g, "*").replace(/÷/g, "/").replace(/[−–]/g, "-")
    .replace(/\*\*/g, "^");
  const tokens = [];
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (/\s/.test(c)) { i++; continue; }
    if (/[0-9.]/.test(c)) {
      const m = /^(\d+\.?\d*|\.\d+)(E[+-]?\d+)?/.exec(text.slice(i));
      if (!m || (m[0].match(/\./g) || []).length > 1) throw new CalcError("Malformed number");
      tokens.push({ t: "num", v: Number(m[0].replace("E", "e")), at: i });
      i += m[0].length;
      if (text[i] === ".") throw new CalcError("Malformed number");
      continue;
    }
    if (/[a-zA-Zπ]/.test(c)) {
      const m = /^(π|[a-zA-Z]+)/.exec(text.slice(i));
      const word = m[0].toLowerCase();
      tokens.push({ t: "name", v: word === "ans" ? "ans" : word, at: i });
      i += m[0].length;
      continue;
    }
    if ("+-*/^()!%²√,".includes(c)) { tokens.push({ t: c, at: i }); i++; continue; }
    throw new CalcError(`Unexpected “${c}”`);
  }
  return tokens;
}

export function evaluate(source, { degrees = true, ans = 0 } = {}) {
  const tokens = tokenize(source);
  if (!tokens.length) throw new CalcError("Empty expression");
  let pos = 0;
  const peek = () => tokens[pos];
  const take = type => {
    const tok = tokens[pos];
    if (!tok || tok.t !== type) {
      if (type === ")") throw new CalcError("Missing closing parenthesis");
      throw new CalcError("Unexpected end of expression");
    }
    pos++;
    return tok;
  };
  const startsOperand = tok => tok && (tok.t === "num" || tok.t === "name" || tok.t === "(" || tok.t === "√");

  function sum() {
    let left = product();
    while (peek() && (peek().t === "+" || peek().t === "-")) {
      const op = tokens[pos++].t;
      const right = product();
      const value = right.percent ? (left.v * right.v) : right.v;
      left = { v: op === "+" ? left.v + value : left.v - value };
    }
    return left;
  }

  function product() {
    let left = unary();
    for (;;) {
      const tok = peek();
      if (tok && (tok.t === "*" || tok.t === "/")) {
        pos++;
        const right = unary();
        if (tok.t === "/" && right.v === 0) throw new CalcError("Cannot divide by zero");
        left = { v: tok.t === "*" ? left.v * right.v : left.v / right.v };
      } else if (startsOperand(tok)) {
        // Implicit multiplication: 2π, 3(4+5), (1+2)(3+4), 2sin(30)
        const right = unary();
        left = { v: left.v * right.v };
      } else return left;
    }
  }

  function unary() {
    const tok = peek();
    if (tok && tok.t === "-") { pos++; const r = unary(); return { v: -r.v, percent: r.percent }; }
    if (tok && tok.t === "+") { pos++; return unary(); }
    return power();
  }

  function power() {
    const base = postfix();
    if (peek() && peek().t === "^") {
      pos++;
      const exponent = unary();
      const v = Math.pow(base.v, exponent.v);
      if (Number.isNaN(v)) throw new CalcError("Result is not a real number");
      return { v };
    }
    return base;
  }

  function postfix() {
    let node = primary();
    for (;;) {
      const tok = peek();
      if (tok && tok.t === "!") { pos++; node = { v: factorial(node.v) }; }
      else if (tok && tok.t === "²") { pos++; node = { v: node.v * node.v }; }
      else if (tok && tok.t === "%") { pos++; node = { v: node.v / 100, percent: true }; }
      else return node;
    }
  }

  function primary() {
    const tok = tokens[pos++];
    if (!tok) throw new CalcError("Unexpected end of expression");
    if (tok.t === "num") return { v: tok.v };
    if (tok.t === "(") { const inner = sum(); take(")"); return { v: inner.v }; }
    if (tok.t === "√") {
      const arg = postfix();
      return { v: FUNCTIONS.sqrt(arg.v) };
    }
    if (tok.t === "name") {
      if (tok.v === "ans") return { v: ans };
      if (tok.v in CONSTANTS) return { v: CONSTANTS[tok.v] };
      if (tok.v in FUNCTIONS) {
        if (!peek() || peek().t !== "(") {
          // sin30 and √-style shorthand: apply to the next postfix operand.
          if (!startsOperand(peek())) throw new CalcError(`${tok.v} needs a value`);
          return { v: FUNCTIONS[tok.v](postfix().v, degrees) };
        }
        pos++;
        const arg = sum();
        take(")");
        return { v: FUNCTIONS[tok.v](arg.v, degrees) };
      }
      throw new CalcError(`Unknown name “${tok.v}”`);
    }
    if (tok.t === ")") throw new CalcError("Unexpected closing parenthesis");
    throw new CalcError("Missing a number");
  }

  const result = sum();
  if (pos < tokens.length) {
    if (tokens[pos].t === ")") throw new CalcError("Unexpected closing parenthesis");
    throw new CalcError("Unexpected input");
  }
  if (!Number.isFinite(result.v)) {
    if (Number.isNaN(result.v)) throw new CalcError("Result is not a real number");
    throw new CalcError("Result is too large");
  }
  return cleanup(result.v);
}

// Removes binary noise such as 0.1 + 0.2 = 0.30000000000000004.
function cleanup(v) {
  if (v === 0) return 0;
  const rounded = Number(v.toPrecision(15));
  return Object.is(rounded, -0) ? 0 : rounded;
}

// For display: grouping, at most 12 significant digits, exponent for extremes.
export function format(v) {
  if (v === 0) return "0";
  const abs = Math.abs(v);
  if (abs >= 1e15 || abs < 1e-9) {
    const [m, e] = v.toExponential(10).split("e");
    return `${m.replace(/\.?0+$/, "")}e${e.replace("+", "")}`;
  }
  const s = Number(v.toPrecision(12)).toString();
  const [int, frac] = s.split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return frac ? `${grouped}.${frac}` : grouped;
}

// Raw form for copying and for reuse as input.
export function plain(v) {
  return Number(v.toPrecision(15)).toString().replace("e+", "E").replace("e-", "E-");
}

// Unclosed parentheses, so the display can show the implied ones.
export function openParens(source) {
  let depth = 0;
  for (const c of source) {
    if (c === "(") depth++;
    else if (c === ")") depth = Math.max(0, depth - 1);
  }
  return depth;
}
