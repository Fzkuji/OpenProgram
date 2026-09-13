/** Join visual line wraps without changing paragraph breaks or ordinary hyphens. */
export function normalizePdfCopy(value: string): string {
  return value.replace(/\r\n?/g, "\n").split(/(\n[ \t]*\n+)/).map((part, index) => {
    if (index % 2) return "\n\n";
    return part.split("\n").reduce((left, line) => {
      const right = line.trimStart();
      left = left.trimEnd();
      if (left.endsWith("\u00ad")) return left.slice(0, -1) + right;
      const boundary = left.slice(-1) + right.slice(0, 1);
      const separator = !left || !right || left.endsWith("-") || /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/u.test(boundary) ? "" : " ";
      return left + separator + right;
    });
  }).join("");
}
