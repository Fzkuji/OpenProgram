/**
 * Pure id / url helpers for the center tab store. No dependency on the
 * store's mutable state — deterministic ids and URL normalization only.
 * Split out of center-tabs-store.ts so both files stay small and this
 * one has no zustand / store coupling.
 */

/** Built-in pages that live in the center as their own tab (Chrome's
 *  chrome://bookmarks / chrome://history). One tab per page, ever. */
export type BuiltinPage = "bookmarks" | "history";

/** New-tab 页不再是单例（Chrome 行为：＋ 想开几个开几个），每个实例一个
 *  唯一 id。时间戳 + 自增序号，避免与持久化恢复的旧 id 撞车。 */
let ntpSeq = 0;
export function nextNtpId(): string {
  return `ntp:${Date.now().toString(36)}:${(ntpSeq++).toString(36)}`;
}

export function nextDraftSessionId(): string {
  return `local_${crypto.randomUUID()}`;
}

export function sessionTabId(sessionId: string): string {
  return `s:${sessionId}`;
}
export function fileTabId(projectId: string, path: string): string {
  return `f:${projectId}:${path}`;
}
export function webTabId(url: string): string {
  return `w:${url}`;
}
export function builtinTabId(page: BuiltinPage): string {
  return `b:${page}`;
}

/** Normalize user input into a browsable http(s) URL: trims, prefixes
 *  bare domains with https://, and rejects every other scheme
 *  (javascript:, data:, file:, …). Returns null when not navigable. */
/** Chrome 地址栏（omnibox）语义：像 URL 的输入按 URL 打开，其余一律
 *  转搜索，绝不静默失败。此前 "bilibili"（无点）会拼成 https://bilibili
 *  → DNS 白屏，含空格的中文词直接被忽略——两种都表现为"浏览器打不开"。
 *  返回 null 仅当输入为空。 */
export function normalizeWebUrl(input: string): string | null {
  const raw = input.trim();
  if (!raw) return null;
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) {
    try {
      const u = new URL(raw);
      if ((u.protocol === "http:" || u.protocol === "https:") && u.hostname) {
        return u.href;
      }
    } catch {
      /* 有 scheme 但解析不了 → 当搜索词 */
    }
    return webSearchUrl(raw);
  }
  // 无 scheme：无空格且主机段像域名（带点 / localhost / IPv6）才按 URL
  const hostish = raw.split("/")[0];
  const urlLike =
    !/\s/.test(raw) &&
    (hostish.includes(".") ||
      /^localhost(:\d+)?$/i.test(hostish) ||
      /^\[[0-9a-f:]+\]/i.test(hostish));
  if (urlLike) {
    try {
      const u = new URL(`https://${raw}`);
      if (u.hostname) return u.href;
    } catch {
      /* fall through to search */
    }
  }
  return webSearchUrl(raw);
}

export function webSearchUrl(query: string): string {
  return `https://www.google.com/search?q=${encodeURIComponent(query)}`;
}

export function hostnameOf(url: string): string {
  try {
    return new URL(url).hostname || url;
  } catch {
    return url;
  }
}
