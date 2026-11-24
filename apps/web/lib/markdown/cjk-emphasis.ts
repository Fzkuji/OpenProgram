import type { MarkedExtension, Tokens } from "marked";

/**
 * Emphasis that CommonMark refuses in CJK prose.
 *
 * CommonMark only closes `**` when the delimiter run is "right-flanking":
 * a closer preceded by punctuation must be followed by whitespace or
 * punctuation. Chinese puts no space after a sentence, so
 * `**目前不能确认。**能确认的是` (closer after `。`, followed by `能`) and
 * `这是**中文，**加粗` are left as literal asterisks — which is exactly the
 * shape models write. The same rule refuses `*斜体。*后`, `***粗斜。***后`
 * and `~~原价，~~现价`.
 *
 * Underscores are refused for another reason: `_` and `__` never open or
 * close inside a word, and CommonMark counts CJK characters as word
 * characters, so `这是__粗体__文字` stays literal.
 *
 * This inline extension accepts such a pair whose inner text does not start
 * or end with whitespace, only where the default rules would refuse it:
 *   - `*`, `**`, `***`, `~~`: a punctuation character touches the inside of
 *     a delimiter, a non-space touches its outside, and a CJK character is
 *     on one side;
 *   - `_`, `__`: a CJK character touches the outside and the inner text
 *     has CJK in it, so `snake_case` and `__init__` stay as they are.
 * Ordinary Latin emphasis keeps the stock behaviour.
 */
const CJK = /[⺀-鿿가-힯豈-﫿＀-￯　-〿]/u;
const CJK_LETTER = /[⺀-⿟㐀-鿿가-힯豈-﫿]/u;
const PUNCT = /[\p{P}\p{S}]/u;

type Kind = "strong" | "em" | "strongEm" | "del";
const DELIMITERS: { open: string; kind: Kind; pattern: RegExp; underscore?: boolean }[] = [
  { open: "***", kind: "strongEm", pattern: /^\*\*\*(?!\s)((?:\\[\s\S]|[^*\n])+?)(?<!\s)\*\*\*/ },
  { open: "**", kind: "strong", pattern: /^\*\*(?!\s)((?:\\\*|[^*\n]|\*(?!\*))+?)(?<!\s)\*\*/ },
  { open: "*", kind: "em", pattern: /^\*(?![\s*])((?:\\\*|[^*\n])+?)(?<![\s*])\*(?!\*)/ },
  { open: "~~", kind: "del", pattern: /^~~(?![\s~])((?:\\~|[^~\n]|~(?!~))+?)(?<![\s~])~~/ },
  { open: "__", kind: "strong", pattern: /^__(?![\s_])((?:\\_|[^_\n])+?)(?<![\s_])__/, underscore: true },
  { open: "_", kind: "em", pattern: /^_(?![\s_])((?:\\_|[^_\n])+?)(?<![\s_])_(?!_)/, underscore: true },
];

function needsHelp(inner: string, before: string, after: string, underscore: boolean): boolean {
  if (underscore) return CJK_LETTER.test(inner) && (CJK.test(before) || CJK.test(after));
  const first = inner.charAt(0);
  const last = inner.charAt(inner.length - 1);
  const openerBlocked = PUNCT.test(first) && before !== "" && !/\s/.test(before) && !PUNCT.test(before);
  const closerBlocked = PUNCT.test(last) && after !== "" && !/\s/.test(after) && !PUNCT.test(after);
  return (openerBlocked || closerBlocked) && (CJK.test(first + last) || CJK.test(before + after));
}

export const cjkEmphasis: MarkedExtension = {
  extensions: [
    {
      name: "cjkEmphasis",
      level: "inline",
      start(src) {
        const i = src.search(/[*_~]/);
        return i < 0 ? undefined : i;
      },
      tokenizer(src, tokens) {
        const prev = tokens[tokens.length - 1] as Tokens.Generic | undefined;
        const before = typeof prev?.raw === "string" ? prev.raw.slice(-1) : "";
        for (const delimiter of DELIMITERS) {
          if (!src.startsWith(delimiter.open)) continue;
          const m = delimiter.pattern.exec(src);
          if (!m) continue;
          const after = src.charAt(m[0].length);
          if (!needsHelp(m[1], before, after, Boolean(delimiter.underscore))) return undefined;
          const inner = this.lexer.inlineTokens(m[1]);
          if (delimiter.kind === "strongEm") {
            return { type: "strong", raw: m[0], text: m[1], tokens: [{ type: "em", raw: m[1], text: m[1], tokens: inner }] };
          }
          return { type: delimiter.kind, raw: m[0], text: m[1], tokens: inner };
        }
        return undefined;
      },
    },
  ],
};
