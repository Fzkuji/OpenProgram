interface OpenFence { char: string; len: number; lineStart: number; infoStart: number; lang: string }

/** The fenced block still open at the end of `src`, if any. Fences follow
 *  CommonMark: up to three spaces of indent, three or more backticks or
 *  tildes, closed by the same character at least as long with nothing after. */
function findOpenFence(src: string): OpenFence | null {
  let open: OpenFence | null = null;
  let pos = 0;
  while (pos <= src.length) {
    const nl = src.indexOf("\n", pos);
    const end = nl === -1 ? src.length : nl;
    const line = src.slice(pos, end);
    if (open) {
      const close = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(line);
      if (close && close[1][0] === open.char && close[1].length >= open.len) open = null;
    } else {
      const fence = /^( {0,3})(`{3,}|~{3,})(.*)$/.exec(line);
      if (fence && !(fence[2][0] === "`" && fence[3].includes("`"))) {
        open = {
          char: fence[2][0],
          len: fence[2].length,
          lineStart: pos,
          infoStart: pos + fence[1].length + fence[2].length,
          lang: fence[3].trim().split(/\s+/)[0] ?? "",
        };
      }
    }
    if (nl === -1) break;
    pos = nl + 1;
  }
  return open;
}

/**
 * Retag a fenced block in `lang` that is still open at the end of `src`
 * (one the model is still streaming) with the info string `partial`, so a
 * renderer can tell an unfinished block from a finished one.
 */
export function retagOpenFence(src: string, lang: string, partial: string): string {
  if (!src.includes(lang)) return src;
  const open = findOpenFence(src);
  if (!open || open.lang !== lang) return src;
  const infoEnd = src.indexOf("\n", open.lineStart);
  const lineEnd = infoEnd === -1 ? src.length : infoEnd;
  return src.slice(0, open.infoStart) + partial + src.slice(lineEnd);
}

/** Close a fenced block left open at the end of `src`, so text joined after
 *  it is not swallowed into the code. */
export function closeOpenFence(src: string): string {
  const open = findOpenFence(src);
  if (!open) return src;
  return `${src}${src.endsWith("\n") ? "" : "\n"}${open.char.repeat(open.len)}`;
}
