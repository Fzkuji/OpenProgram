import type { MarkedExtension, Tokens } from "marked";

/**
 * Formulas as markdown tokens.
 *
 * A formula is found by the lexer, so code spans and code blocks keep their
 * dollars and backslashes, and it renders as one element that holds its own
 * source: `<span class="md-math">$x$</span>`. KaTeX typesets exactly those
 * elements (typesetFormulas); it never scans the text for delimiters, so
 * dollars that are not a formula stay text.
 *
 * Which dollars open a formula follows Pandoc: `$` must be followed by a
 * non-space, the closing `$` preceded by a non-space and not followed by a
 * digit. So prices in a sentence (`价格 $5，折后 $3`, `$5 and $10`) are
 * text, and `$x$`, `$a_b$`, `$E=mc^2$` are formulas. `\(…\)`, `\[…\]` and
 * `$$…$$` are always formulas.
 */
const INLINE = /^(?:\$\$(?!\$)([\s\S]+?)\$\$|\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\]|\$(?![\s$])((?:\\[\s\S]|[^\\$\n])+?)(?<!\s)\$(?!\d))/;
const BLOCK = /^ {0,3}(?:\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\])[ \t]*(?:\n|$)/;

function escape(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

interface FormulaToken extends Tokens.Generic { display: boolean }

function element(token: FormulaToken, block: boolean): string {
  const source = escape(token.raw.trim());
  if (block) return `<div class="md-math" data-display="true">${source}</div>\n`;
  return token.display
    ? `<span class="md-math" data-display="true">${source}</span>`
    : `<span class="md-math">${source}</span>`;
}

export const mathMarkdown: MarkedExtension = {
  extensions: [
    {
      name: "formulaBlock",
      level: "block",
      // Where a formula line cuts a paragraph. marked asks from one past
      // the paragraph's start, so only a line after a newline counts.
      start(src) {
        const i = src.search(/\n {0,3}(?:\$\$|\\\[)/);
        return i < 0 ? undefined : i + 1;
      },
      tokenizer(src) {
        const m = BLOCK.exec(src);
        if (!m) return undefined;
        return { type: "formulaBlock", raw: m[0], display: true };
      },
      renderer(token) { return element(token as FormulaToken, true); },
    },
    {
      name: "formula",
      level: "inline",
      start(src) {
        const i = src.search(/\$|\\[([]/);
        return i < 0 ? undefined : i;
      },
      tokenizer(src) {
        const m = INLINE.exec(src);
        if (!m) return undefined;
        return { type: "formula", raw: m[0], display: m[1] !== undefined || m[3] !== undefined };
      },
      renderer(token) { return element(token as FormulaToken, false); },
    },
  ],
};

/** The TeX inside a formula element's source, and whether it is displayed. */
export function formulaSource(text: string): { tex: string; display: boolean } {
  const s = text.trim();
  if (s.startsWith("$$") && s.endsWith("$$") && s.length >= 4) return { tex: s.slice(2, -2), display: true };
  if (s.startsWith("\\[") && s.endsWith("\\]")) return { tex: s.slice(2, -2), display: true };
  if (s.startsWith("\\(") && s.endsWith("\\)")) return { tex: s.slice(2, -2), display: false };
  if (s.startsWith("$") && s.endsWith("$") && s.length >= 2) return { tex: s.slice(1, -1), display: false };
  return { tex: s, display: false };
}
