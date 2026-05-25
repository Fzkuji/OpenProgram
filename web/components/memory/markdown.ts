/**
 * Markdown helpers for the Memory page — frontmatter parsing,
 * [[wikilink]] expansion, and the marked.parse() wrapper.
 */
import { marked } from "marked";

/** Parse YAML-ish frontmatter from raw markdown. Returns the
 *  ``{ frontmatter, body }`` pair — empty frontmatter when the
 *  text doesn't start with the ``---\n`` delimiter. */
export function parseFrontmatter(raw: string): { frontmatter: Record<string, string>; body: string } {
  if (!raw.startsWith("---\n") && !raw.startsWith("---\r\n")) {
    return { frontmatter: {}, body: raw };
  }
  const end = raw.indexOf("\n---", 4);
  if (end < 0) return { frontmatter: {}, body: raw };
  const fmText = raw.slice(4, end);
  const body = raw.slice(end + 4).replace(/^\s*\n/, "");
  const fm: Record<string, string> = {};
  for (const line of fmText.split("\n")) {
    const m = line.match(/^([\w-]+):\s*(.*)$/);
    if (m) fm[m[1]] = m[2].trim().replace(/^["']|["']$/g, "");
  }
  return { frontmatter: fm, body };
}

/** Expand ``[[wikilink]]`` / ``[[target|alias]]`` syntax into
 *  ``<a class="wikilink" data-target=...>`` anchors that the
 *  preview-pane click delegation listens for. */
export function expandWikilinks(md: string): string {
  return md.replace(/\[\[([^\]|]+)(\|([^\]]+))?\]\]/g, (_m, target, _p, alias) => {
    const label = alias || target;
    const slug = String(target).trim();
    return `<a class="wikilink" data-target="${slug}">${label}</a>`;
  });
}

/** marked.parse() wrapper that runs ``expandWikilinks`` first and
 *  falls back to an escaped <pre> on parse error. */
export function renderMarkdown(md: string): string {
  try {
    return marked.parse(expandWikilinks(md), { breaks: true, async: false }) as string;
  } catch {
    return `<pre>${md.replace(/</g, "&lt;")}</pre>`;
  }
}
