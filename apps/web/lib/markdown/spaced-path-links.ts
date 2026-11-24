import type { MarkedExtension } from "marked";

/**
 * `[name](/Users/me/My Files/deck.pptx)`: a link to a local file whose path
 * has a space in it.
 *
 * CommonMark ends a link destination at the first space unless it is
 * wrapped in `<…>`, so the stock parser leaves the whole link as literal
 * text. Models write local paths exactly this way (folders like
 * "ICDM 2026"), and nothing else can be meant by a destination that starts
 * like a path, so this inline extension accepts it when the destination is
 * a root, home, `file://` or drive path and the default rules would refuse
 * it. One level of parentheses inside the path ("deck (1).pptx") is kept.
 * The href is percent-encoded; links resolve it back to the path
 * (lib/files/prose-file-paths.ts).
 */
const LINK = /^\[((?:\\.|[^\[\]\n])+)\]\(\s*((?:file:\/\/|\/(?!\/)|~\/|[A-Za-z]:[\\/])(?:[^()\n]|\([^()\n]*\))*?)\s*\)/;

export const spacedPathLinks: MarkedExtension = {
  extensions: [
    {
      name: "spacedPathLink",
      level: "inline",
      start(src) {
        const i = src.indexOf("[");
        return i < 0 ? undefined : i;
      },
      tokenizer(src) {
        const m = LINK.exec(src);
        if (!m || !/\s/.test(m[2])) return undefined;
        return {
          type: "link",
          raw: m[0],
          href: encodeURI(m[2]),
          title: null,
          text: m[1],
          tokens: this.lexer.inlineTokens(m[1]),
        };
      },
    },
  ],
};
