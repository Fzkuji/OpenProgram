import { decodeHTMLAttribute } from "entities";

/** Classify document destinations before they reach browser navigation. */
export type MarkdownDestination =
  | { kind: "local" | "fragment" | "external"; href: string }
  | { kind: "rejected" };

export function markdownTokenDestination(reference: string, image = false): MarkdownDestination {
  return markdownDestination(decodeHTMLAttribute(reference), image);
}

/** DOM and resolver addresses are already entity-decoded; never decode again. */
export function markdownDestination(reference: string, image = false): MarkdownDestination {
  const raw = reference.trim();
  if (!raw || /[\x00-\x1f\x7f\\]/.test(raw)) return { kind: "rejected" };
  if (raw.startsWith("#")) return image ? { kind: "rejected" } : { kind: "fragment", href: raw };
  const href = raw.startsWith("//") ? `https:${raw}` : raw;
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(href)?.[1].toLowerCase();
  if (scheme) {
    if (!["http", "https", ...(image ? [] : ["mailto"])].includes(scheme)) return { kind: "rejected" };
    try {
      const url = new URL(href);
      if (scheme !== "mailto" && !url.hostname) return { kind: "rejected" };
    } catch { return { kind: "rejected" }; }
    return { kind: "external", href };
  }
  // A colon in the first segment must not become a browser scheme after URL
  // normalization. Backslashes above are rejected rather than slash-normalized.
  if (href.split(/[/?#]/, 1)[0].includes(":")) return { kind: "rejected" };
  return { kind: "local", href };
}

/** Resolve only local paths. Queries are ignored, never authorization inputs;
 * fragments stay separate and percent-encoded # characters remain filenames. */
export function resolveMarkdownFile(documentPath: string, reference: string): { path: string; fragment: string } | null {
  const destination = markdownDestination(reference);
  if (destination.kind !== "local") return null;
  const raw = destination.href;
  const hash = raw.indexOf("#");
  const fragment = hash < 0 ? "" : raw.slice(hash);
  let target = (hash < 0 ? raw : raw.slice(0, hash)).split("?", 1)[0];
  try { target = decodeURIComponent(target); } catch { return null; }
  if (!target || /[\x00-\x1f\x7f\\]/.test(target)) return null;
  const doc = documentPath.replace(/\\/g, "/");
  const absolute = doc.startsWith("/");
  const parts = (target.startsWith("/") ? [] : doc.split("/").slice(0, -1)).filter(Boolean);
  for (const segment of target.split("/")) {
    if (!segment || segment === ".") continue;
    if (segment === "..") {
      if (!parts.length) return null;
      parts.pop();
    } else parts.push(segment);
  }
  if (!parts.length) return null;
  return { path: (absolute ? "/" : "") + parts.join("/"), fragment };
}

/** Compatibility path-only view for existing callers. */
export function resolveMarkdownReference(documentPath: string, reference: string): string | null {
  return resolveMarkdownFile(documentPath, reference)?.path ?? null;
}
