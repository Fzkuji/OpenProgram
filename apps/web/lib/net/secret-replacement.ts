const PRINTABLE_ASCII = /^[\x20-\x7e]+$/;

/** Return a user-entered replacement secret, never a displayed mask. */
export function normalizeSecretReplacement(
  input: string,
  displayedMask = "",
): string | null {
  const value = input.trim();
  if (!value || !PRINTABLE_ASCII.test(value)) return null;
  if (displayedMask && value === displayedMask.trim()) return null;
  return value;
}
