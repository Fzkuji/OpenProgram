import type { MarkedExtension, Tokens } from "marked";

/**
 * `**bold**` that CommonMark refuses in CJK prose.
 *
 * CommonMark only closes `**` when the delimiter run is "right-flanking":
 * a closer preceded by punctuation must be followed by whitespace or
 * punctuation. Chinese puts no space after a sentence, so
 * `**目前不能确认。**能确认的是` (closer after `。`, followed by `能`) and
 * `这是**中文，**加粗` are left as literal asterisks — which is exactly the
 * shape models write. This inline extension accepts a `**…**` pair whose
 * inner text does not start or end with whitespace, and only when the
 * default rules would reject it (a punctuation character touches the
 * inside of a delimiter and a CJK character touches the outside), so
 * ordinary Latin emphasis keeps the stock behaviour.
 */
const CJK = /[⺀-鿿가-힯豈-﫿＀-￯　-〿]/u;
const PUNCT = /[\p{P}\p{S}]/u;
const STRONG = /^\*\*(?!\s)((?:\\\*|[^*\n]|\*(?!\*))+?)(?<!\s)\*\*/;

function needsHelp(src: string, inner: string, before: string): boolean {
  const after = src.charAt(inner.length + 4);
  const first = inner.charAt(0);
  const last = inner.charAt(inner.length - 1);
  const openerBlocked = PUNCT.test(first) && before !== "" && !/\s/.test(before) && !PUNCT.test(before);
  const closerBlocked = PUNCT.test(last) && after !== "" && !/\s/.test(after) && !PUNCT.test(after);
  return (openerBlocked || closerBlocked) && (CJK.test(first + last) || CJK.test(before + after));
}

export const cjkEmphasis: MarkedExtension = {
  extensions: [
    {
      name: "cjkStrong",
      level: "inline",
      start(src) {
        const i = src.indexOf("**");
        return i < 0 ? undefined : i;
      },
      tokenizer(src, tokens) {
        const m = STRONG.exec(src);
        if (!m) return undefined;
        const prev = tokens[tokens.length - 1] as Tokens.Generic | undefined;
        const before = typeof prev?.raw === "string" ? prev.raw.slice(-1) : "";
        if (!needsHelp(src, m[1], before)) return undefined;
        return {
          type: "strong",
          raw: m[0],
          text: m[1],
          tokens: this.lexer.inlineTokens(m[1]),
        };
      },
    },
  ],
};
