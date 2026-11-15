/**
 * Pasted spans of a sent user message (Codex `text_elements`): the full
 * pasted text stays in the message the model reads; these records only
 * tell the bubble which spans to show collapsed.
 *
 * Offsets count from the END of the message. The user's text is always
 * the tail of the stored content — attachment markers are prepended and
 * may be rewritten by the server (it fills in file paths) — so distances
 * from the end survive that, where offsets from the start would not.
 * Each record also carries the span's first characters; a span whose
 * text no longer matches is ignored and the message renders in full.
 */
export interface PastedTextElement {
  /** Distance from the end of the message to the span start. */
  tail_start: number;
  /** Distance from the end of the message to the span end. */
  tail_end: number;
  label: string;
  /** First characters of the span, to verify the offsets still match. */
  head: string;
}

export const PASTE_HEAD_CHARS = 32;
const MAX_ELEMENTS = 20;

export function pastedSpanLabel(lines: number): string {
  return `Pasted · ${lines} ${lines === 1 ? "line" : "lines"}`;
}

/** Records for spans [start, end) of `body`, the final tail of the message. */
export function elementsForBody(
  body: string,
  spans: { start: number; end: number; label: string }[],
): PastedTextElement[] {
  return spans.slice(0, MAX_ELEMENTS).map((span) => ({
    tail_start: body.length - span.start,
    tail_end: body.length - span.end,
    label: span.label,
    head: body.slice(span.start, span.start + PASTE_HEAD_CHARS),
  }));
}

/** Untrusted (server / history) value → validated records. */
export function readPastedElements(raw: unknown): PastedTextElement[] {
  if (!Array.isArray(raw)) return [];
  const out: PastedTextElement[] = [];
  for (const item of raw.slice(0, MAX_ELEMENTS)) {
    if (!item || typeof item !== "object") continue;
    const { tail_start, tail_end, label, head } = item as Record<string, unknown>;
    if (
      typeof tail_start === "number" && typeof tail_end === "number"
      && Number.isInteger(tail_start) && Number.isInteger(tail_end)
      && tail_start > tail_end && tail_end >= 0
      && typeof label === "string" && typeof head === "string"
    ) {
      out.push({ tail_start, tail_end, label: label.slice(0, 80), head: head.slice(0, PASTE_HEAD_CHARS) });
    }
  }
  return out;
}

/** Placeholder that survives attachment parsing and markdown untouched. */
export function pasteSentinel(index: number): string {
  return `OPPASTESPAN${index}X`;
}

/** Swap each still-matching span for a sentinel. Returns the rewritten
 *  content and the collapsed spans in sentinel order. */
export function collapsePastedSpans(
  content: string,
  elements: PastedTextElement[],
): { content: string; spans: { label: string; text: string }[] } {
  const len = content.length;
  const valid = elements
    .map((el) => ({ el, start: len - el.tail_start, end: len - el.tail_end }))
    .filter(({ el, start, end }) =>
      start >= 0 && end <= len && start < end
      && content.slice(start, start + el.head.length) === el.head)
    .sort((a, b) => a.start - b.start)
    .filter((item, i, all) => i === 0 || item.start >= all[i - 1].end);
  const spans: { label: string; text: string }[] = [];
  let out = "";
  let last = 0;
  for (const { el, start, end } of valid) {
    out += content.slice(last, start) + pasteSentinel(spans.length);
    spans.push({ label: el.label, text: content.slice(start, end) });
    last = end;
  }
  return { content: out + content.slice(last), spans };
}
