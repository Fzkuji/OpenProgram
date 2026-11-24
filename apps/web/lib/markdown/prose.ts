import type { MarkedExtension } from "marked";
import { cjkBlocks } from "./cjk-blocks.ts";
import { cjkEmphasis } from "./cjk-emphasis.ts";
import { cjkLinks } from "./cjk-links.ts";
import { spacedPathLinks } from "./spaced-path-links.ts";

/**
 * What every renderer of prose markdown adds to marked — chat replies, the
 * user's own messages, file previews, memory, skills and A2UI text — so
 * they read Chinese the same way. Formulas are separate (./math.ts): a
 * renderer that typesets them adds `mathMarkdown` as well.
 *
 * Each part only takes what the stock rules would show as literal text,
 * so English and well-spaced markdown render exactly as before:
 *   - cjk-emphasis: bold, italic and strikethrough next to CJK punctuation;
 *   - cjk-links: bare addresses end at full-width punctuation, and
 *     `[文字]（地址）` is a link;
 *   - spaced-path-links: `[name](/a path/with spaces.pptx)`;
 *   - cjk-blocks: `##标题`, `1.第一项`, `-第一项`.
 */
export const proseMarkdown: MarkedExtension[] = [cjkEmphasis, cjkLinks, spacedPathLinks, cjkBlocks];
