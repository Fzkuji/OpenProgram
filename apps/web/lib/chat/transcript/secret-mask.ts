/**
 * Secrets in user-written text: access tokens and private keys that a
 * user pastes into a message. Masking is display-only — the message the
 * model reads is unchanged — and a masked secret shows its first
 * characters followed by dots, the way a password field would.
 *
 * Only formats with a recognisable prefix are matched, so ordinary prose
 * and identifiers are never masked by accident.
 */

const BOUNDARY = "(?<![A-Za-z0-9_-])";
const PATTERNS = [
  "github_pat_[A-Za-z0-9_]{22,}",
  "gh[pousr]_[A-Za-z0-9]{36,}",
  "sk-ant-[A-Za-z0-9_-]{20,}",
  "sk-(?:proj-)?[A-Za-z0-9_-]{20,}",
  "(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}",
  "xox[abprs]-[A-Za-z0-9-]{10,}",
  "AKIA[0-9A-Z]{16}",
  "AIza[0-9A-Za-z_-]{35}",
  "hf_[A-Za-z0-9]{30,}",
  "npm_[A-Za-z0-9]{36}",
  "glpat-[A-Za-z0-9_-]{20,}",
  "eyJ[A-Za-z0-9_-]{10,}\\.eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}",
];
const PEM = "-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\\s\\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----";
const SECRET_RE = new RegExp(`${PEM}|${BOUNDARY}(?:${PATTERNS.join("|")})(?![A-Za-z0-9_-])`, "g");

const MAX_DOTS = 48;

export interface SecretSpan {
  start: number;
  end: number;
}

export function findSecrets(text: string): SecretSpan[] {
  const out: SecretSpan[] = [];
  for (const m of text.matchAll(SECRET_RE)) {
    out.push({ start: m.index, end: m.index + m[0].length });
  }
  return out;
}

/** The characters a masked secret still shows, and how many dots follow. */
export function secretParts(secret: string): { shown: string; dots: number } {
  if (secret.startsWith("-----")) return { shown: `${secret.split("\n", 1)[0]} `, dots: 12 };
  const shown = secret.length >= 24 ? 8 : 4;
  return { shown: secret.slice(0, shown), dots: Math.min(secret.length - shown, MAX_DOTS) };
}

/** The secret's first characters, then one dot per hidden character. */
export function maskSecret(secret: string): string {
  const { shown, dots } = secretParts(secret);
  return shown + "•".repeat(dots);
}

/** `text` with every secret replaced by its masked form. */
export function maskSecrets(text: string): string {
  return text.replace(SECRET_RE, maskSecret);
}

/** Placeholder that survives attachment parsing and markdown untouched. */
export function secretSentinel(index: number): string {
  return `OPSECRETSPAN${index}X`;
}

/** Swap each secret for a sentinel. Returns the rewritten text and the
 *  secrets in sentinel order. */
export function collapseSecrets(text: string): { content: string; secrets: string[] } {
  const secrets: string[] = [];
  const content = text.replace(SECRET_RE, (secret) => {
    secrets.push(secret);
    return secretSentinel(secrets.length - 1);
  });
  return { content, secrets };
}
