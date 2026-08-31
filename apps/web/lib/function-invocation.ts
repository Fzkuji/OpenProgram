import type { AgenticFunction, FnParam } from "@/lib/session-store";

const INTERNAL_PARAM_NAMES = new Set([
  "runtime",
  "callback",
  "exec_runtime",
  "review_runtime",
]);

export function userFunctionParams(fn: AgenticFunction): FnParam[] {
  return (fn.params_detail || []).filter(
    (param) => !INTERNAL_PARAM_NAMES.has(param.name)
      && (!param.hidden || param.advanced),
  );
}

type ValidArguments = {
  ok: true;
  kwargs: Record<string, unknown>;
};

type InvalidArguments = {
  ok: false;
  error: string;
  errorParam?: string;
};

export type NormalizedFunctionArguments = ValidArguments | InvalidArguments;

function splitTopLevel(source: string, delimiter: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let start = 0;
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    if (char === "[") depth += 1;
    else if (char === "]") depth = Math.max(0, depth - 1);
    else if (char === delimiter && depth === 0) {
      parts.push(source.slice(start, index));
      start = index + 1;
    }
  }
  parts.push(source.slice(start));
  return parts;
}

function parseTypeParts(source: string): string[] {
  const normalized = source.trim().replaceAll("typing.", "");
  if (!normalized) return [];
  const optional = normalized.match(/^Optional\[(.*)\]$/i);
  if (optional) return [...parseTypeParts(optional[1]), "none"];
  const union = normalized.match(/^Union\[(.*)\]$/i);
  if (union) {
    return splitTopLevel(union[1], ",").flatMap(parseTypeParts);
  }
  const unionParts = splitTopLevel(normalized, "|");
  if (unionParts.length > 1) return unionParts.flatMap(parseTypeParts);
  return [normalized.toLowerCase()];
}

function typeParts(param: FnParam): string[] {
  return parseTypeParts(String(param.type || ""));
}

function acceptsNull(parts: string[]): boolean {
  return parts.some(
    (part) => part === "none" || part === "null" || part === "undefined",
  );
}

function strictNumber(value: string, integer: boolean): number | null {
  const pattern = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
  if (!pattern.test(value)) return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  if (integer && !Number.isSafeInteger(parsed)) return null;
  return parsed;
}

function normalizeValue(
  param: FnParam,
  raw: unknown,
): { ok: true; value: unknown } | InvalidArguments {
  const parts = typeParts(param);
  if (raw === null) {
    return acceptsNull(parts)
      ? { ok: true, value: null }
      : { ok: false, error: `${param.name} cannot be null`, errorParam: param.name };
  }

  const isBool = parts.includes("bool") || parts.includes("boolean");
  const isInt = parts.includes("int") || parts.includes("integer");
  const isFloat = parts.includes("float") || parts.includes("number");
  const isString = parts.length === 0
    || parts.includes("str")
    || parts.includes("string");

  let value: unknown;
  if (isBool && typeof raw === "boolean") {
    value = raw;
  } else if (typeof raw === "string") {
    const trimmed = raw.trim();
    const lower = trimmed.toLowerCase();
    if (isBool && ["true", "1", "false", "0"].includes(lower)) {
      value = lower === "true" || lower === "1";
    } else if (isInt) {
      const parsed = strictNumber(trimmed, true);
      if (parsed !== null) value = parsed;
    }
    if (value === undefined && isFloat) {
      const parsed = strictNumber(trimmed, false);
      if (parsed !== null) value = parsed;
    }
    if (value === undefined && isString) value = raw;
  } else if (typeof raw === "number") {
    if (isInt && Number.isSafeInteger(raw)) value = raw;
    else if (isFloat && Number.isFinite(raw)) value = raw;
  }

  if (value === undefined) {
    if (!isBool && !isInt && !isFloat && !isString && typeof raw === "string") {
      return {
        ok: false,
        error: `${param.name} uses unsupported parameter type: ${param.type}`,
        errorParam: param.name,
      };
    } else if (isBool) {
      return {
        ok: false,
        error: `${param.name} must be true or false`,
        errorParam: param.name,
      };
    } else if (isInt || isFloat) {
      return {
        ok: false,
        error: `${param.name} must be ${isInt ? "an integer" : "a number"}`,
        errorParam: param.name,
      };
    } else {
      return {
        ok: false,
        error: `${param.name} must be a string`,
        errorParam: param.name,
      };
    }
  }

  if (param.options?.length && !param.options.includes(String(value))) {
    return {
      ok: false,
      error: `${param.name} must be one of: ${param.options.join(", ")}`,
      errorParam: param.name,
    };
  }
  return { ok: true, value };
}

export function normalizeFunctionArguments(
  fn: AgenticFunction,
  values: Record<string, unknown>,
): NormalizedFunctionArguments {
  const params = userFunctionParams(fn);
  const byName = new Map(params.map((param) => [param.name, param]));
  for (const name of Object.keys(values)) {
    if (!byName.has(name)) {
      return { ok: false, error: `Unknown parameter: ${name}`, errorParam: name };
    }
  }

  const kwargs: Record<string, unknown> = {};
  for (const param of params) {
    const present = Object.prototype.hasOwnProperty.call(values, param.name);
    const raw = values[param.name];
    if (!present) {
      if (param.required) {
        return {
          ok: false,
          error: `Missing required parameter: ${param.name}`,
          errorParam: param.name,
        };
      }
      continue;
    }
    if (param.required && typeof raw === "string" && raw.trim() === "") {
      return {
        ok: false,
        error: `Missing required parameter: ${param.name}`,
        errorParam: param.name,
      };
    }
    const normalized = normalizeValue(param, raw);
    if (!normalized.ok) return normalized;
    kwargs[param.name] = normalized.value;
  }
  return { ok: true, kwargs };
}

type ParsedLiteral = { value: unknown; end: number };

function skipSpace(source: string, start: number): number {
  let index = start;
  while (index < source.length && /\s/.test(source[index])) index += 1;
  return index;
}

function parseQuotedString(source: string, start: number): ParsedLiteral | null {
  const quote = source[start];
  if (quote !== '"' && quote !== "'") return null;
  let index = start + 1;
  let value = "";
  const escapes: Record<string, string> = {
    n: "\n", r: "\r", t: "\t", b: "\b", f: "\f", v: "\v", "0": "\0",
    "\\": "\\", "\"": "\"", "'": "'",
  };
  while (index < source.length) {
    const char = source[index++];
    if (char === quote) return { value, end: index };
    if (char !== "\\") {
      value += char;
      continue;
    }
    if (index >= source.length) return null;
    const escaped = source[index++];
    if (escaped === "u") {
      const hex = source.slice(index, index + 4);
      if (!/^[0-9a-f]{4}$/i.test(hex)) return null;
      value += String.fromCharCode(Number.parseInt(hex, 16));
      index += 4;
    } else {
      value += escapes[escaped] ?? `\\${escaped}`;
    }
  }
  return null;
}

function parseLiteral(source: string, start: number): ParsedLiteral | null {
  const quoted = parseQuotedString(source, start);
  if (quoted) return quoted;
  const rest = source.slice(start);
  const number = rest.match(/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?/i);
  if (number) return { value: Number(number[0]), end: start + number[0].length };
  for (const [token, value] of [
    ["True", true], ["False", false], ["None", null],
    ["true", true], ["false", false], ["null", null],
  ] as const) {
    if (rest.startsWith(token)) return { value, end: start + token.length };
  }
  return null;
}

function valueForForm(value: unknown): string {
  if (typeof value === "boolean") return value ? "True" : "False";
  if (value === null) return "";
  return String(value);
}

function outerCallEnd(source: string, openIndex: number): number | null {
  let depth = 0;
  let quote = "";
  let escaped = false;
  for (let index = openIndex; index < source.length; index += 1) {
    const char = source[index];
    if (quote) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === quote) quote = "";
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
      continue;
    }
    if (char === "(") depth += 1;
    else if (char === ")") {
      depth -= 1;
      if (depth === 0) return index + 1;
    }
  }
  return null;
}

export type FunctionInvocationParseResult =
  | { kind: "none" }
  | {
      kind: "invalid";
      fn: AgenticFunction;
      error: string;
      errorParam?: string;
      prefill: Record<string, string>;
    }
  | {
      kind: "valid";
      fn: AgenticFunction;
      kwargs: Record<string, unknown>;
    };

export function parseFunctionInvocation(
  text: string,
  functions: AgenticFunction[],
): FunctionInvocationParseResult {
  const source = text.trim();
  const head = source.match(/^([A-Za-z_][\w.-]*)\s*\(/);
  if (!head) return { kind: "none" };
  const fn = functions.find((candidate) => candidate.name === head[1]);
  if (!fn) return { kind: "none" };

  const openIndex = source.indexOf("(", head[1].length);
  const callEnd = outerCallEnd(source, openIndex);
  if (callEnd !== null && skipSpace(source, callEnd) !== source.length) {
    return { kind: "none" };
  }

  const values = new Map<string, unknown>();
  const prefill = new Map<string, string>();
  let index = openIndex + 1;
  const invalid = (error: string, errorParam?: string): FunctionInvocationParseResult => ({
    kind: "invalid",
    fn,
    error,
    errorParam,
    prefill: Object.fromEntries(prefill),
  });
  let closed = false;

  index = skipSpace(source, index);
  if (source[index] === ")") {
    index += 1;
    closed = true;
  }
  else {
    while (index < source.length) {
      index = skipSpace(source, index);
      const key = source.slice(index).match(/^([A-Za-z_]\w*)/);
      if (!key) return invalid("Expected a named parameter");
      const name = key[1];
      if (values.has(name)) {
        return invalid(`Duplicate parameter: ${name}`, name);
      }
      index += name.length;
      index = skipSpace(source, index);
      if (source[index] !== "=") {
        return invalid(`Expected '=' after ${name}`, name);
      }
      index = skipSpace(source, index + 1);
      const literal = parseLiteral(source, index);
      if (!literal) return invalid(`Invalid literal for ${name}`, name);
      values.set(name, literal.value);
      prefill.set(name, valueForForm(literal.value));
      index = skipSpace(source, literal.end);
      if (source[index] === ")") {
        index += 1;
        closed = true;
        break;
      }
      if (source[index] !== ",") {
        return invalid(`Expected ',' or ')' after ${name}`, name);
      }
      index = skipSpace(source, index + 1);
      if (source[index] === ")") {
        index += 1;
        break;
      }
    }
  }

  if (!closed) return invalid("Expected ')' to close the function call");
  if (skipSpace(source, index) !== source.length) {
    // Recognition is whole-input only. A registered-looking call followed by
    // explanation is ordinary chat, not a partially consumed invocation.
    return { kind: "none" };
  }
  const normalized = normalizeFunctionArguments(fn, Object.fromEntries(values));
  if (!normalized.ok) {
    return invalid(normalized.error, normalized.errorParam);
  }
  return { kind: "valid", fn, kwargs: normalized.kwargs };
}
