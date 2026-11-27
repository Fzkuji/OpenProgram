/** Whether two JSON-like values (plain objects, arrays, primitives) hold the
 *  same data. A store write that changes nothing keeps its old references,
 *  so nothing that reads them renders again: a settings reply echoing what
 *  the composer just set re-rendered the whole composer, about 5 ms, three
 *  times in the first 50 ms of the effort label's slide (traced). */
export function sameData(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) {
    const other = b as unknown[];
    return a.length === other.length && a.every((value, index) => sameData(value, other[index]));
  }
  if (Object.getPrototypeOf(a) !== Object.prototype && Object.getPrototypeOf(a) !== null) return false;
  if (Object.getPrototypeOf(b) !== Object.prototype && Object.getPrototypeOf(b) !== null) return false;
  const left = a as Record<string, unknown>;
  const right = b as Record<string, unknown>;
  const keys = Object.keys(left).filter((key) => left[key] !== undefined);
  if (keys.length !== Object.keys(right).filter((key) => right[key] !== undefined).length) return false;
  return keys.every((key) => sameData(left[key], right[key]));
}
