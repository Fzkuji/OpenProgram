import type { MarkedExtension } from "marked";

/**
 * Headings and lists written without the space CommonMark wants.
 *
 * Chinese has no spaces between words, and models writing Chinese often
 * drop the one after the marker: `##标题`, `1.第一项`, `-第一项`. Each then
 * shows its marker as text. Here a run of such lines, at a line start and
 * followed directly by a CJK character, is read with the space put back.
 * Being block tokens, they are never looked for inside code.
 *
 * Kept as written: `#话题#今天` (a hashtag: one `#` closed by another), `1.5倍` (a digit
 * follows the dot), `1、`/`（1）` numbering and `•` bullets (plain text
 * that reads as intended), and `*` without a space (that is emphasis).
 */
const CJK = "[⺀-鿿가-힯豈-﫿“‘「『（【《]";
const HEADING = new RegExp(`^ {0,3}(#{1,6})(?=${CJK})([^\\n]*)(?:\\n|$)`, "u");
const HASHTAG = /^ {0,3}#[^#\s][^#\n]*#/;
const ITEM = new RegExp(`^( {0,3})(\\d{1,3}[.)]|[-+])(?=${CJK})`, "u");
const ITEM_LINE = new RegExp(`^ {0,3}(?:\\d{1,3}[.)]|[-+])(?: |(?=${CJK}))`, "u");
const START = new RegExp(`\\n {0,3}(?:#{1,6}|\\d{1,3}[.)]|[-+])${CJK}`, "u");

export const cjkBlocks: MarkedExtension = {
  extensions: [
    {
      name: "cjkBlock",
      level: "block",
      start(src) {
        const i = src.search(START);
        // marked asks from one past the paragraph's start: only a line
        // after a newline cuts it.
        return i < 0 ? undefined : i + 1;
      },
      tokenizer(src) {
        const heading = HEADING.exec(src);
        if (heading) {
          if (heading[1].length === 1 && HASHTAG.test(heading[0])) return undefined;
          return {
            type: "cjkBlock", raw: heading[0],
            tokens: this.lexer.blockTokens(heading[0].replace(/#+/, (marks) => `${marks} `), []),
          };
        }
        if (!ITEM_LINE.test(src)) return undefined;
        const lines: string[] = [];
        let raw = "";
        let fixed = false;
        for (const line of src.split("\n")) {
          if (!ITEM_LINE.test(line) && !(lines.length && /^ {2,}\S/.test(line))) break;
          if (ITEM.test(line)) fixed = true;
          lines.push(line.replace(ITEM, "$1$2 "));
          raw += line + "\n";
        }
        // A list whose items all have their space is the stock list's.
        if (!fixed) return undefined;
        if (raw.length > src.length) raw = src;
        return { type: "cjkBlock", raw, tokens: this.lexer.blockTokens(lines.join("\n") + "\n", []) };
      },
      renderer(token) {
        return this.parser.parse(token.tokens ?? []);
      },
    },
  ],
};
