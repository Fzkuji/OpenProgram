/** True when every key in `patch` already matches `cur` (setHead-style). */
export function messagePatchUnchanged<T extends object>(
  cur: T,
  patch: Partial<T>,
): boolean {
  for (const key of Object.keys(patch) as (keyof T)[]) {
    if (cur[key] !== patch[key]) return false;
  }
  return true;
}
