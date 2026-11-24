/** What a failed request says, always as text. A server's `detail` is a
 *  sentence, a list of what is wrong with the body, or an object with a
 *  `code`: put on screen as it came, the last two break the page that
 *  draws them. */
export function errorDetailText(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail.trim() || fallback;
  if (Array.isArray(detail)) {
    const lines = detail.map(item => {
      if (typeof item === "string") return item;
      if (!item || typeof item !== "object") return "";
      const { msg, loc } = item as { msg?: unknown; loc?: unknown };
      if (typeof msg !== "string") return "";
      // Where in the body: "body.args.0" without the leading "body".
      const where = Array.isArray(loc) ? loc.filter(part => part !== "body").join(".") : "";
      return where ? `${where}: ${msg}` : msg;
    }).filter(Boolean);
    return lines.length ? lines.join("; ") : fallback;
  }
  if (detail && typeof detail === "object") {
    const { message, code } = detail as { message?: unknown; code?: unknown };
    if (typeof message === "string" && message.trim()) return message;
    if (typeof code === "string" && code.trim()) return code;
  }
  return fallback;
}

/** The code a failure carries when its `detail` is an object, for callers that
 *  have their own sentence for it. */
export function errorDetailCode(detail: unknown): string | undefined {
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return undefined;
  const { code } = detail as { code?: unknown };
  return typeof code === "string" && code.trim() ? code : undefined;
}
