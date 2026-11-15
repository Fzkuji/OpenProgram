/**
 * Split a streaming reply's markdown into top-level chunks, so a bubble can
 * keep the rendered DOM of every finished chunk and re-render only the last,
 * still-open one per delta.
 *
 * The chunks concatenate back to the source. A boundary is a blank line
 * followed by a line that starts a new top-level block on its own: not
 * indented (list or code continuation), not a further item of a list (one
 * list must stay one list), and not inside a code fence or a display formula. Sources whose
 * rendering joins distant parts (link reference or footnote definitions,
 * raw HTML blocks) stay whole. A completed reply renders the whole text, so
 * this only shapes what is drawn while the reply streams.
 */

export const JOINED_SOURCE = /^ {0,3}\[[^\]\n]*\]:|\[\^|^ {0,3}</m;
const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const LIST_ITEM = /^(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)/;

/** Whether a display formula (`$$…$$`, `\[…\]`, `\(…\)`) is still open at
 *  the end of `text`, pairing delimiters in order the way the renderer does. */
export function formulaOpen(text: string): boolean {
  if ((text.match(/\$\$/g)?.length ?? 0) % 2) return true;
  for (const [open, close] of [["\\[", "\\]"], ["\\(", "\\)"]] as const) {
    let from = 0;
    for (;;) {
      const start = text.indexOf(open, from);
      if (start < 0) break;
      const end = text.indexOf(close, start + open.length);
      if (end < 0) return true;
      from = end + close.length;
    }
  }
  return false;
}

export function splitMarkdownBlocks(src: string): string[] {
  if (!src || JOINED_SOURCE.test(src)) return [src];
  const lines = src.split(/(?<=\n)/);
  const chunks: string[] = [];
  let start = 0;
  let offset = 0;
  let fence: { char: string; length: number } | null = null;
  let afterBlank = true;
  // Whether the last top-level block began as a list item.
  let inList = false;
  for (const line of lines) {
    const blank = !line.trim();
    const head = !fence && afterBlank && !blank && !/^\s/.test(line);
    if (
      head && offset > start && !(inList && LIST_ITEM.test(line))
      && !formulaOpen(src.slice(start, offset))
    ) {
      chunks.push(src.slice(start, offset));
      start = offset;
    }
    if (head) inList = LIST_ITEM.test(line);
    const marker = FENCE.exec(line)?.[1];
    if (fence) {
      if (marker && marker[0] === fence.char && marker.length >= fence.length && !line.slice(line.indexOf(marker) + marker.length).trim()) {
        fence = null;
      }
    } else if (marker) {
      fence = { char: marker[0], length: marker.length };
    }
    afterBlank = blank;
    offset += line.length;
  }
  chunks.push(src.slice(start));
  return chunks;
}
