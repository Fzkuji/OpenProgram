/**
 * Stateless formatting helpers for the Skills Discovery panel.
 */
import type { CatalogEntry } from "@/lib/skills-store";

export function slugFromUrl(url: string): string {
  // Mirror backend _default_namespace logic so the "installed" badge in the
  // catalog matches what the install endpoint will write.
  const gh = url.match(
    /^(?:https?:\/\/github\.com\/|github:\/\/)[^/]+\/([^/@]+?)(?:\.git)?(?:\/|@|$)/,
  );
  if (gh) return gh[1].toLowerCase();
  try {
    const h = new URL(url).hostname;
    return h.replace(/\./g, "-").toLowerCase() || "remote";
  } catch {
    return "remote";
  }
}


export function hasStats(entries: CatalogEntry[]): boolean {
  return entries.some(
    (e) => (e.stars || 0) > 0 || (e.downloads || 0) > 0 || (e.updated_at || 0) > 0,
  );
}

export function fmtCount(n: number | undefined): string {
  const v = n || 0;
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
  if (v >= 1_000) return (v / 1_000).toFixed(1) + "k";
  return String(v);
}

export function relTime(ms: number | undefined, locale: "en" | "zh" = "en"): string {
  if (!ms) return "";
  const diff = Date.now() - ms;
  const d = Math.floor(diff / 86_400_000);
  if (locale === "zh") {
    if (d < 1) return "今天";
    if (d < 30) return d + " 天前";
    const mo = Math.floor(d / 30);
    if (mo < 12) return mo + " 个月前";
    return Math.floor(d / 365) + " 年前";
  }
  if (d < 1) return "today";
  if (d < 30) return d + "d ago";
  const mo = Math.floor(d / 30);
  if (mo < 12) return mo + "mo ago";
  return Math.floor(d / 365) + "y ago";
}


export function hostname(u: string): string {
  try {
    return new URL(u).hostname;
  } catch {
    return "";
  }
}
