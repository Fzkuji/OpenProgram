/**
 * Where a Topic paragraph came from, marked in the preview.
 *
 * `/api/memory/topics/<path>` names each paragraph's origin: `cited` (it
 * cites the conversation it rests on), `person` (added without a citation,
 * in Settings or by hand) or `outside` (its file was written by another
 * tool, and OpenProgram only reads it). A cited paragraph shows its
 * footnotes; the other two get a Nova badge whose words say it all, beside
 * the paragraph's hidden block anchor. A whole file written outside gets
 * one badge by its title instead of one per paragraph.
 */
import { badgeVariants } from "@/components/ui/badge";

export type Origin = "cited" | "person" | "outside";

export interface ParagraphOrigin {
  memory_id: string;
  line: number;
  derived?: boolean;
  origin: Origin;
}

export interface TopicOrigins {
  origin?: "runtime" | "outside";
  paragraphs?: ParagraphOrigin[];
}

const escapeHtml = (value: string) =>
  value.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);

/** Put a badge after the block anchor of each paragraph a person added. */
export function markOrigins(html: string, origins: TopicOrigins | null | undefined, label: string): string {
  if (!origins?.paragraphs?.length || origins.origin === "outside") return html;
  const marked = new Set(
    origins.paragraphs
      .filter((row) => row.origin === "person" && !row.derived)
      .map((row) => row.memory_id),
  );
  if (!marked.size) return html;
  const badge = `<span data-slot="badge" data-origin="person" class="${escapeHtml(badgeVariants({ variant: "secondary" }))} ml-[6px] align-middle">${escapeHtml(label)}</span>`;
  return html.replace(
    /<span\b[^>]*\bid="\^([A-Za-z0-9][A-Za-z0-9-]*)"[^>]*><\/span>/g,
    (anchor, id: string) => (anchor.includes("data-block-id") && marked.has(id) ? `${anchor}${badge}` : anchor),
  );
}
