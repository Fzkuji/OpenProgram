/**
 * What an `@` in the composer can name besides files: the conversation's web pages,
 * the other applications' windows shown in tabs, and the running apps. Each pick is written into the draft so the model reads
 * the exact page or app it means instead of guessing "this tab". Plain
 * TypeScript so tests load it.
 */

export interface FileMention { kind?: "file"; path: string; is_dir: boolean }
export interface TabMention { kind: "tab"; tabId: string; title: string; url: string; faviconUrl?: string }
export interface AppMention { kind: "app"; name: string; bundle_id: string; icon: string }
/** Another application's window shown in a tab: naming it hands that exact
 *  window to the Agent, which operates it on the `app` target by its `window_id`. */
export interface WindowMention { kind: "window"; tabId: string; app: string; title: string; windowId: number }
export type Mention = FileMention | TabMention | AppMention | WindowMention;

/** Where tab and app mentions come from; without it the menu lists files only. */
/** A web page this conversation uses, as its Resources list shows it. */
export interface MentionPage { id: string; title: string; url: string; faviconUrl?: string }

export interface MentionSources {
  /** The conversation's web pages, every one Resources lists. */
  pages: () => Promise<readonly MentionPage[]>;
  tabs: () => readonly { id: string; kind: string; title?: string; url?: string; faviconUrl?: string;
    nativeWindowId?: number; nativeWindowApp?: string }[];
  /** The running apps, read once per `mention` (one @ being typed). */
  apps: (mention: string) => Promise<readonly { name: string; bundle_id: string; icon: string }[]>;
}

export const GROUP_LIMIT = 5;

function host(url: string): string {
  try { return new URL(url).host; } catch { return url; }
}

/** A path-like partial is a file search, so tabs and apps stay out of it. */
function wantsOthers(partial: string): boolean {
  return !/[/\\]/.test(partial);
}

/** The conversation's own pages, all of them: other conversations' pages
 *  are not this one's to name. */
export function matchTabs(pages: readonly MentionPage[], partial: string): TabMention[] {
  if (!wantsOthers(partial)) return [];
  const q = partial.toLowerCase();
  return pages
    .filter((page) => !!page.url)
    .map((page) => ({ kind: "tab" as const, tabId: page.id, title: (page.title || host(page.url)).trim(), url: page.url, faviconUrl: page.faviconUrl }))
    .filter((tab) => !q || tab.title.toLowerCase().includes(q) || tab.url.toLowerCase().includes(q));
}

export function matchWindows(tabs: ReturnType<MentionSources["tabs"]>, partial: string): WindowMention[] {
  if (!wantsOthers(partial)) return [];
  const q = partial.toLowerCase();
  return tabs
    .filter((tab) => tab.kind === "window" && Number.isInteger(tab.nativeWindowId))
    .map((tab) => ({ kind: "window" as const, tabId: tab.id, app: (tab.nativeWindowApp || "").trim(),
      title: (tab.title || tab.nativeWindowApp || "").trim(), windowId: tab.nativeWindowId! }))
    .filter((item) => !q || item.title.toLowerCase().includes(q) || item.app.toLowerCase().includes(q))
    .slice(0, GROUP_LIMIT);
}

export function matchApps(apps: Awaited<ReturnType<MentionSources["apps"]>>, partial: string): AppMention[] {
  if (!wantsOthers(partial)) return [];
  const q = partial.toLowerCase();
  return apps
    .filter((app) => app.bundle_id && (!q || app.name.toLowerCase().includes(q) || app.bundle_id.toLowerCase().includes(q)))
    .map((app) => ({ kind: "app" as const, name: app.name, bundle_id: app.bundle_id, icon: app.icon }))
    .slice(0, GROUP_LIMIT);
}

/** The text that replaces `@partial` in the draft. Only a file keeps its
 *  `@`: an `@token` (or `@"quoted path"`) is resolved as a file path when
 *  the message is sent. */
export function mentionInsert(item: Mention): string {
  if (item.kind === "tab") return `[${item.title.replace(/[[\]]/g, "")}](${item.url}) `;
  if (item.kind === "app") return `${item.name} (${item.bundle_id}) `;
  if (item.kind === "window") {
    // The window is operated through the local `app` target, so the text
    // names that target and the subject a call gives it.
    const title = item.title && item.title !== item.app ? ` "${item.title.replace(/"/g, "'")}"` : "";
    const app = item.app ? `, app "${item.app.replace(/"/g, "'")}"` : "";
    return `${item.app || "app"} window${title} (target "app"${app}, window_id ${item.windowId}) `;
  }
  // A path with whitespace is quoted so it stays one token; a folder
  // leaves the quote open for the rest of the path.
  if (/\s/.test(item.path)) return item.is_dir ? `@"${item.path}/` : `@"${item.path}" `;
  return item.is_dir ? `@${item.path}/` : `@${item.path} `;
}

export function mentionKey(item: Mention): string {
  if (item.kind === "tab") return `tab:${item.tabId}`;
  if (item.kind === "app") return `app:${item.bundle_id}`;
  if (item.kind === "window") return `window:${item.windowId}`;
  return `file:${item.path}`;
}

export function mentionHost(url: string): string {
  return host(url);
}
