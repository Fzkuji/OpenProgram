/** HTML files open as pages in the desktop browser tab, which reads them
 *  straight from this machine's disk through file:// — so their relative
 *  CSS, images and scripts load as they would in Chrome. That needs the
 *  desktop shell and a worker on the same machine; anywhere else HTML
 *  stays a source file. */

const HTML_EXTENSIONS = new Set(["html", "htm"]);

export function isHtmlPath(path: string): boolean {
  const name = path.replace(/\\/g, "/").split("/").pop() ?? "";
  const dot = name.lastIndexOf(".");
  return dot > 0 && HTML_EXTENSIONS.has(name.slice(dot + 1).toLowerCase());
}

export function canBrowseLocalFiles(): boolean {
  if (typeof window === "undefined") return false;
  if (!(window as { openprogramDesktop?: unknown }).openprogramDesktop) return false;
  const host = window.location.hostname;
  return host === "localhost" || host === "127.0.0.1" || host === "[::1]" || host.endsWith(".localhost");
}

/** file:// URL of an absolute POSIX path; each segment is escaped, so
 *  "#", "?" and spaces in names stay part of the path. */
export function fileUrlFromPath(absolutePath: string): string {
  return "file://" + absolutePath.split("/").map(encodeURIComponent).join("/");
}

/** Absolute path behind a file:// URL, or null for any other URL. */
export function localPathFromUrl(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "file:" || (parsed.hostname && parsed.hostname !== "localhost")) return null;
    return decodeURIComponent(parsed.pathname);
  } catch {
    return null;
  }
}
