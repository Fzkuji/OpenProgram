/**
 * Settings > Versions: what the page shows, in the words it shows it, and the
 * two notices about versions. No React here, so each sentence is testable.
 */
import { queryOptions } from "@tanstack/react-query";

import { jsonFetch } from "@/lib/net/fetch-client";

type Text = (en: string, zh: string) => string;

export interface KeptVersion {
  id: string;
  number: number | null;
  summary: string;
  built_at: number | null;
  commit?: string;
  current: boolean;
  can_undo_change: boolean;
}

export interface SavedEdits {
  stamp: string;
  files: number;
  found_at: number | null;
}

export interface VersionsOverview {
  managed: boolean;
  current?: {
    id: string;
    number: number | null;
    since: number | null;
    on_probation: boolean;
    returned_from: { id: string; number: number | null; at: number } | null;
  };
  fallback?: { id: string; number: number | null } | null;
  versions: KeptVersion[];
  unapplied: SavedEdits[];
}

export const versionsQuery = queryOptions({
  queryKey: ["settings", "versions"] as const,
  queryFn: () => jsonFetch<VersionsOverview>("/api/versions"),
  staleTime: 0,
  refetchOnWindowFocus: true,
});

export function versionName(number: number | null | undefined, text: Text): string {
  return number ? text(`Version ${number}`, `版本 ${number}`) : text("This version", "这个版本");
}

/** "Nov 8, 10:42" in the reader's language; empty when the time is unknown. */
export function versionDate(seconds: number | null | undefined, text: Text): string {
  if (!seconds) return "";
  return new Intl.DateTimeFormat(text("en-US", "zh-CN"), {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(seconds * 1000));
}

export function runningDescription(page: VersionsOverview, text: Text): string {
  const current = page.current;
  if (!current) return "";
  const when = versionDate(current.since, text);
  if (current.on_probation) {
    const fallback = versionName(page.fallback?.number, text);
    return page.fallback
      ? text(`Just switched in and still being checked. If a check fails, OpenProgram returns to ${fallback}.`,
        `刚切换过来，还在检查。检查没通过的话，OpenProgram 会回到${fallback}。`)
      : text("Just switched in and still being checked.", "刚切换过来，还在检查。");
  }
  if (current.returned_from) {
    const failed = versionName(current.returned_from.number, text);
    const at = versionDate(current.returned_from.at, text);
    return text(`${failed} stopped starting, so OpenProgram came back to this version on ${at}.`,
      `${failed}启动不了，OpenProgram 在 ${at} 回到了这个版本。`);
  }
  return when
    ? text(`Passed its tests and has been working since ${when}.`, `已通过测试，自 ${when} 起一直正常运行。`)
    : text("Passed its tests and is working.", "已通过测试，正常运行中。");
}

export function fallbackDescription(page: VersionsOverview, text: Text): string {
  if (!page.fallback) {
    return text("There is no earlier version to return to yet.", "还没有可以回去的更早版本。");
  }
  const fallback = versionName(page.fallback.number, text);
  const current = versionName(page.current?.number, text);
  return text(
    `${fallback}. If ${current} stops starting, OpenProgram goes back to it by itself. You don't need to do anything.`,
    `${fallback}。如果${current}启动不了，OpenProgram 会自己回到它，你不用做什么。`,
  );
}

/** The summary line of a kept version, then when it was made. */
export function keptDescription(version: KeptVersion, text: Text): string {
  const summary = version.summary.trim();
  const sentence = summary && !/[.!?。！？]$/.test(summary) ? `${summary}.` : summary;
  return [sentence, versionDate(version.built_at, text)].filter(Boolean).join(" ");
}

export function savedEditsTitle(edits: SavedEdits, text: Text): string {
  const when = versionDate(edits.found_at, text);
  const files = text(edits.files === 1 ? "1 file" : `${edits.files} files`, `${edits.files} 个文件`);
  return when ? text(`${files}, found ${when}`, `${files}，发现于 ${when}`) : files;
}

export interface VersionNotice {
  id: string;
  kind: "returned" | "direct_edits";
  at: number;
  number?: number | null;
  session_id?: string | null;
  files?: number;
}

/** How long a notice stays up: long enough to read and reach its link. */
export const NOTICE_DURATION_MS = 12000;
const NOTICE_GAP_MS = 400;

export function noticeToast(notice: VersionNotice, text: Text): { message: string; link: { label: string; href: string } } {
  if (notice.kind === "direct_edits") {
    const count = notice.files ?? 0;
    return {
      message: text(
        `Your direct edits to ${count === 1 ? "1 file" : `${count} files`} are saved, but not in use.`,
        `你直接改动的 ${count} 个文件已经保存，但没有生效。`,
      ),
      link: { label: text("Open Versions", "打开版本页"), href: "/settings/versions" },
    };
  }
  const failed = notice.number
    ? text(`Version ${notice.number}`, `版本 ${notice.number} `)
    : text("The newest version", "最新版本");
  return {
    message: text(
      `${failed} didn't work, so OpenProgram went back to the last good version.`,
      `${failed}没有正常工作，OpenProgram 已回到上一个可用版本。`,
    ),
    link: {
      label: text("See why", "查看原因"),
      href: notice.session_id ? `/s/${encodeURIComponent(notice.session_id)}` : "/settings/versions",
    },
  };
}

export interface NoticeRunnerOptions {
  fetchNotices: () => Promise<VersionNotice[]>;
  acknowledge: (ids: string[]) => Promise<unknown>;
  show: (notice: VersionNotice) => void;
  wait?: (ms: number) => Promise<void>;
}

/** Shows pending notices strictly one at a time, each once per page load. */
export function createNoticeRunner({ fetchNotices, acknowledge, show, wait }: NoticeRunnerOptions): () => Promise<void> {
  const pause = wait ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const shown = new Set<string>();
  let running = false;
  let again = false;
  return async function poll() {
    if (running) {
      again = true;
      return;
    }
    running = true;
    try {
      do {
        again = false;
        for (const notice of await fetchNotices()) {
          if (shown.has(notice.id)) continue;
          shown.add(notice.id);
          show(notice);
          void acknowledge([notice.id]).catch(() => undefined);
          await pause(NOTICE_DURATION_MS + NOTICE_GAP_MS);
        }
      } while (again);
    } catch {
      // The worker is restarting; the next connection reads the notices again.
    } finally {
      running = false;
    }
  };
}
