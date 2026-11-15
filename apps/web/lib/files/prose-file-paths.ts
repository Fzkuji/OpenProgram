/**
 * Which strings in a reply could name a local file.
 *
 * A reply names the files it made as inline code (`exports/plan.pptx`) or
 * as a markdown link ([plan.pptx](/Users/me/exports/plan.pptx:12)). These
 * helpers turn such a string into the path to ask the worker about, or
 * null when it cannot be a path. They only shape the question: a string
 * becomes a link once the worker answers that the file exists
 * (`/api/file-paths`), so a code span like `os.path.join` that happens to
 * look like a name never turns into a dead link.
 */

/** First path segments the app's own routes use; a link to one of them
 *  (`/settings`, `/s/<chat>#<message>`) is a page of the app, not a file. */
const APP_ROUTES = new Set([
  "agents", "applications", "chat", "chats", "functions", "history", "mcp", "memory",
  "pending", "plugins", "programs", "projects", "s", "scheduler", "settings", "skills",
]);

const MAX_PATH = 1024;

/** A Windows drive (`C:\`, `C:/`), which is not a URL scheme. */
const DRIVE = /^[A-Za-z]:[\\/]/;

/** A trailing `:12`, `:12:3` or `#L12` (an editor location) is not part of the file name. */
function withoutLocation(path: string): string {
  return path.replace(/:\d+(?::\d+)?$/, "").replace(/#L\d+(?:-L?\d+)?$/, "");
}

/** Whether a string has a path's shape: a folder separator, or a name
 *  with an extension. Spaces are allowed (folder names have them); line
 *  breaks, shell and markup characters are not. */
function pathShaped(path: string): boolean {
  if (!path || path.length > MAX_PATH) return false;
  if (/[\x00-\x1f\x7f<>|"`*?$={};]/.test(path)) return false;
  if (/^[a-z][a-z0-9+.-]*:/i.test(path) && !DRIVE.test(path)) return false;
  const name = path.replace(/\/+$/, "").split("/").pop() ?? "";
  if (!name || name === "." || name === "..") return false;
  // A trailing slash names a folder, which nothing here opens.
  if (path.endsWith("/")) return false;
  if (path.includes("/")) return !/\s{2,}/.test(path) && !/^\s|\s$/.test(path);
  return /^[^\s/][^/]*\.[A-Za-z0-9]{1,8}$/.test(name) && !/\s/.test(name.slice(name.lastIndexOf(".")));
}

/** The path an inline code span names, or null. */
export function codeSpanPath(text: string): string | null {
  let path = text.trim();
  if (path.startsWith("file://")) path = path.slice(7);
  path = withoutLocation(path);
  return pathShaped(path) ? path : null;
}

/** The path a markdown link's href could name, to ask the worker about,
 *  or null for a web address, an in-page anchor or a page of the app.
 *
 *  A bare relative href (`notes/plan.md`, but also `docs.example.ai` or
 *  `example.com/path`, a web address written without its scheme) is only
 *  a question: it becomes a file link once the worker confirms the file,
 *  and until then keeps the link's own behaviour (see `localPathHref`). */
export function linkHrefPath(href: string): string | null {
  let path = href.trim();
  if (!path || path.startsWith("#") || path.startsWith("//")) return null;
  if (path.startsWith("file://")) path = path.slice(7);
  else if (/^[a-z][a-z0-9+.-]*:/i.test(path) && !DRIVE.test(path)) return null;
  try { path = decodeURIComponent(path); } catch { return null; }
  path = withoutLocation(path);
  if (isAppRoute(path)) return null;
  return pathShaped(path) ? path : null;
}

/** The path an href names when it can only be a local file: `file://`, a
 *  root (`/`, not an app page), the home folder (`~/`), the current or
 *  parent folder (`./`, `../`) or a drive (`C:\`). Such a link has no
 *  page to go to, so a click is kept from the router and looked up, and
 *  a file that is gone says so. Anything else is null. */
export function localPathHref(href: string): string | null {
  const raw = href.trim();
  if (!/^(?:file:\/\/|\/(?!\/)|~\/|\.\.?\/)/i.test(raw) && !DRIVE.test(raw)) return null;
  return linkHrefPath(raw);
}

/** A root-relative href whose first segment is one of the app's routes. */
export function isAppRoute(href: string): boolean {
  if (!href.startsWith("/") || href.startsWith("//")) return false;
  const first = href.slice(1).split(/[/?#]/, 1)[0];
  return APP_ROUTES.has(first);
}

/** The file's name, the last segment of either separator. */
export function fileName(path: string): string {
  return path.replace(/\\/g, "/").split("/").pop() || path;
}

/** `path` relative to `root` when it lies inside it, else null. */
export function relativeTo(path: string, root: string | null | undefined): string | null {
  if (!root) return null;
  const base = root.replace(/\\/g, "/").replace(/\/+$/, "");
  const normalized = path.replace(/\\/g, "/");
  return normalized.startsWith(base + "/") ? normalized.slice(base.length + 1) : null;
}
