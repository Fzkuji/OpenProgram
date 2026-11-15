/**
 * ```visual blocks inside chat markdown.
 *
 * A reply can draw: HTML or SVG in a fenced ```visual block is shown in
 * place of the code, as a live view the model wrote (a chart, a diagram,
 * option cards with buttons, a slider to explore). The view runs in a
 * sandboxed iframe of the server's /visual-frame page (visual-frames.ts
 * mounts it), never in this page.
 *
 * Here the rendered markdown only swaps the code block for a mount point
 * holding the source; a block still streaming shows a placeholder until
 * its closing fence arrives, because its scripts can only run once whole.
 */
import { retagOpenFence } from "./open-fence.ts";

export const VISUAL_LANG = "visual";
/** Info string given to a ```visual fence still open at the end of the text. */
export const VISUAL_PARTIAL_LANG = "visual-partial";

export function markOpenVisualFence(src: string): string {
  return retagOpenFence(src, VISUAL_LANG, VISUAL_PARTIAL_LANG);
}

const VISUAL_BLOCK = new RegExp(
  `<pre><code class="language-(${VISUAL_LANG}|${VISUAL_PARTIAL_LANG})">([\\s\\S]*?)<\\/code><\\/pre>`,
  "g",
);

/** Swap ```visual code blocks for mount points. The body is the escaped
 *  source marked emitted, kept escaped in a hidden <pre> (not <pre><code>,
 *  so the code-card pass leaves it alone); the mounter reads its text. */
export function withVisuals(html: string): string {
  if (!html.includes(`language-${VISUAL_LANG}`)) return html;
  return html.replace(VISUAL_BLOCK, (_block, lang: string, body: string) => {
    if (lang === VISUAL_PARTIAL_LANG) return visualPending();
    if (!body.trim()) return "";
    return `<div class="op-visual" data-visual="pending"><pre class="op-visual-source" hidden>${body}</pre></div>`;
  });
}

/** Placeholder while the block streams: a quiet frame with skeleton bars,
 *  no words, so it needs no translation. */
export function visualPending(): string {
  return '<div class="op-visual op-visual-drawing" aria-busy="true">'
    + '<span class="op-visual-skeleton"></span><span class="op-visual-skeleton"></span><span class="op-visual-skeleton"></span>'
    + "</div>";
}
