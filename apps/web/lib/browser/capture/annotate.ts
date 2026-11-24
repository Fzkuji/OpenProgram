// Annotate mode in a web tab: the notes the user pinned on a page, as the
// chat gets them. The desktop host runs the overlay and takes the
// screenshot (apps/desktop/main/web-views/annotate-overlay.js and
// webtab-annotate.js); this file only words the message. Plain TypeScript
// so tests load it.

/** One Adjust value the user moved: `font-size 28px → 34px`. */
export interface AnnotationAdjust {
  property: string;
  from: string;
  to: string;
}

export interface AnnotationNote {
  n: number;
  kind: "element" | "region";
  /** Short selector of an element note (`h1#firstHeading`). */
  selector: string;
  tag: string;
  /** The element's visible text, clipped. */
  text: string;
  /** What the user wrote. */
  comment: string;
  /** Where the note sat in the viewport when it was sent (CSS px). */
  rect: { x: number; y: number; width: number; height: number };
  adjust: AnnotationAdjust[];
}

/** What the desktop host reports from a web tab in annotate mode. */
export type AnnotateEvent =
  | { id: string; type: "state"; active: boolean; count: number }
  | {
    id: string;
    type: "send";
    url: string;
    title: string;
    notes: AnnotationNote[];
    /** PNG data URL of the page with the pins drawn; empty when capture failed. */
    screenshot: string;
  };

/** The words the overlay shows inside the page, in the interface language. */
export interface AnnotateLabels {
  hint: string;
  placeholder: string;
  delete: string;
  adjust: string;
  size: string;
  spacing: string;
  color: string;
  countOne: string;
  /** `{n}` is replaced by the count. */
  countMany: string;
  discard: string;
  send: string;
}

/** The App's colours, so the cards inside the page look like the App. */
export interface AnnotateTheme {
  bg: string;
  border: string;
  text: string;
  muted: string;
  accent: string;
}

const QUOTE_LIMIT = 80;

function clip(value: string, limit: number): string {
  const flat = value.replace(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit - 1)}…` : flat;
}

/** `font-size 28px → 34px`. */
export function adjustText(change: AnnotationAdjust): string {
  return `${change.property} ${change.from} → ${change.to}`;
}

/** What a note points at: the element's selector and text, or the region. */
export function noteTarget(note: AnnotationNote): string {
  if (note.kind === "region") {
    const { x, y, width, height } = note.rect;
    return `region ${width}×${height} at (${x}, ${y})`;
  }
  const selector = note.selector.replace(/`/g, "");
  const name = selector ? `\`${selector}\`` : `<${note.tag || "element"}>`;
  const quote = clip(note.text, QUOTE_LIMIT);
  return quote ? `${name} ("${quote.replace(/"/g, "'")}")` : name;
}

/** The one user message Send to chat writes: the page, then each note by
 *  number, as the pins on the screenshot are numbered. */
export function annotationMessage(
  page: { url: string; title?: string },
  notes: readonly AnnotationNote[],
  withScreenshot: boolean,
): string {
  const title = page.title?.replace(/[[\]]/g, "").trim();
  const head = title ? `Notes on [${title}](${page.url}):` : `Notes on ${page.url}:`;
  const lines = notes.map((note, index) => {
    const comment = note.comment.trim().replace(/\s*\n\s*/g, " ");
    const row = `${index + 1}. ${noteTarget(note)}${comment ? `: ${comment}` : ""}`;
    return note.adjust.length
      ? `${row}\n   Adjust: ${note.adjust.map(adjustText).join("; ")}`
      : row;
  });
  const tail = withScreenshot
    ? ["", "The attached screenshot shows the page with each note's number pinned where it applies."]
    : [];
  return [head, "", ...lines, ...tail].join("\n");
}

/** `notes-github.com.png`: the site, safe as a file name. */
export function annotationFileName(url: string): string {
  let site = "page";
  try { site = new URL(url).hostname || site; } catch { /* keep "page" */ }
  site = site.replace(/^www\./, "").replace(/[^A-Za-z0-9.-]+/g, "-").slice(0, 60) || "page";
  return `notes-${site}.png`;
}
