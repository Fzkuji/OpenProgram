import type { MarkedExtension } from "marked";

/**
 * Links in Chinese prose.
 *
 * Bare web addresses: GFM extends one to the next space, and Chinese has
 * no space after it, so `访问 https://example.com/a。下一句` linked
 * `https://example.com/a。下一句`, and `（见 https://a.com）后面` took the
 * closing parenthesis and the rest of the sentence. An address ends here at
 * the first full-width punctuation mark (`。，、；：！？（）【】《》「」“”…`),
 * which no address is written with, and at a CJK character still in the
 * host name (`https://a.com下一句`). CJK in a path stays
 * (`zh.wikipedia.org/wiki/中文`), as it does everywhere else.
 *
 * `[文档]（https://…）`: a link written with full-width parentheses, as
 * Chinese input methods type them, is a link when the target is a web
 * address or a local path.
 */
const FULLWIDTH_PUNCT = /[　-〿！-／：-＠［-｀｛-･—‘’“”…·]/u;
const CJK = /[⺀-鿿가-힯豈-﫿]/u;

function webAddress(src: string): string | null {
  const m = /^(?:https?:\/\/|www\.)[^\s<]+/i.exec(src);
  if (!m) return null;
  const candidate = m[0];
  const scheme = /^https?:\/\//i.exec(candidate)?.[0].length ?? 0;
  let end = candidate.length;
  for (let i = scheme; i < candidate.length; i++) {
    if (FULLWIDTH_PUNCT.test(candidate[i])) { end = i; break; }
  }
  const hostEnd = candidate.slice(scheme).search(/[/?#]/);
  const host = candidate.slice(scheme, hostEnd < 0 ? end : Math.min(end, scheme + hostEnd));
  const cjkInHost = host.search(CJK);
  if (cjkInHost >= 0) end = Math.min(end, scheme + cjkInHost);
  if (end === candidate.length) return null;
  let url = candidate.slice(0, end);
  // GFM's own trailing rules: final punctuation and an unbalanced `)`.
  for (;;) {
    if (/[?!.,:*_~'";]$/.test(url)) { url = url.slice(0, -1); continue; }
    if (url.endsWith(")") && (url.match(/\(/g)?.length ?? 0) < (url.match(/\)/g)?.length ?? 0)) { url = url.slice(0, -1); continue; }
    break;
  }
  return url.length > scheme && !/^www\.?$/i.test(url) ? url : null;
}

const FULLWIDTH_LINK = /^\[((?:\\.|[^\[\]\n])+)\]（\s*((?:https?:\/\/|file:\/\/|\/(?!\/)|~\/|[A-Za-z]:[\\/])[^（）\n]*?)\s*）/;

export const cjkLinks: MarkedExtension = {
  tokenizer: {
    url(src) {
      const url = webAddress(src);
      if (!url) return false;
      return {
        type: "link",
        raw: url,
        text: url,
        href: /^www\./i.test(url) ? `http://${url}` : url,
        tokens: [{ type: "text", raw: url, text: url }],
      };
    },
  },
  extensions: [
    {
      name: "fullwidthLink",
      level: "inline",
      start(src) {
        const i = src.indexOf("[");
        return i < 0 ? undefined : i;
      },
      tokenizer(src) {
        const m = FULLWIDTH_LINK.exec(src);
        if (!m) return undefined;
        const target = m[2].trim();
        return {
          type: "link",
          raw: m[0],
          href: /^https?:/i.test(target) ? target : encodeURI(target),
          title: null,
          text: m[1],
          tokens: this.lexer.inlineTokens(m[1]),
        };
      },
    },
  ],
};
