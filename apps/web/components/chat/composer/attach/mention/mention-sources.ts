/**
 * What an `@` in the composer can name besides files: the open web tabs and
 * the running apps. Each pick is written into the draft so the model reads
 * the exact page or app it means instead of guessing "this tab". Plain
 * TypeScript so tests load it.
 */

export interface FileMention { kind?: "file"; path: string; is_dir: boolean }
export interface TabMention { kind: "tab"; tabId: string; title: string; url: string; faviconUrl?: string }
export interface AppMention { kind: "app"; name: string; bundle_id: string; icon: string }
export type Mention = FileMention | TabMention | AppMention;

/** Where tab and app mentions come from; without it the menu lists files only. */
export interface MentionSources {
  tabs: () => readonly { id: string; kind: string; title?: string; url?: string; faviconUrl?: string }[];
  apps: () => Promise<readonly { name: string; bundle_id: string; icon: string }[]>;
}

export const GROUP_LIMIT = 5;

function host(url: string): string {
  try { return new URL(url).host; } catch { return url; }
}

/** A path-like partial is a file search, so tabs and apps stay out of it. */
function wantsOthers(partial: string): boolean {
  return !/[/\\]/.test(partial);
}

export function matchTabs(tabs: ReturnType<MentionSources["tabs"]>, partial: string): TabMention[] {
  if (!wantsOthers(partial)) return [];
  const q = partial.toLowerCase();
  return tabs
    .filter((tab) => tab.kind === "web" && /^https?:/i.test(tab.url || ""))
    .map((tab) => ({ kind: "tab" as const, tabId: tab.id, title: (tab.title || host(tab.url!)).trim(), url: tab.url!, faviconUrl: tab.faviconUrl }))
    .filter((tab) => !q || tab.title.toLowerCase().includes(q) || tab.url.toLowerCase().includes(q))
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
  // A path with whitespace is quoted so it stays one token; a folder
  // leaves the quote open for the rest of the path.
  if (/\s/.test(item.path)) return item.is_dir ? `@"${item.path}/` : `@"${item.path}" `;
  return item.is_dir ? `@${item.path}/` : `@${item.path} `;
}

export function mentionKey(item: Mention): string {
  if (item.kind === "tab") return `tab:${item.tabId}`;
  if (item.kind === "app") return `app:${item.bundle_id}`;
  return `file:${item.path}`;
}

export function mentionHost(url: string): string {
  return host(url);
}
