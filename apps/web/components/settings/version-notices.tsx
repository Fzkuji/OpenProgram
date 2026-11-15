"use client";

/**
 * Tells the owner, once, when OpenProgram went back to an earlier version or
 * set direct edits aside. Reads again each time the connection to the server
 * returns, because both happen across a restart. Renders nothing itself: the
 * notices are ordinary toasts.
 */
import { useEffect } from "react";

import { showToast } from "@/lib/format-utils/toast";
import { useTranslation } from "@/lib/i18n";
import { jsonFetch } from "@/lib/net/fetch-client";
import { createNoticeRunner, NOTICE_DURATION_MS, noticeToast, type VersionNotice } from "@/lib/settings/versions";

export function VersionNotices() {
  const { text } = useTranslation();
  useEffect(() => {
    const poll = createNoticeRunner({
      fetchNotices: () => jsonFetch<{ notices?: VersionNotice[] }>("/api/versions/notices").then((data) => data.notices ?? []),
      acknowledge: (ids) => jsonFetch("/api/versions/notices/ack", { method: "POST", body: JSON.stringify({ ids }) }),
      show: (notice) => {
        const { message, link } = noticeToast(notice, text);
        showToast(message, { tone: "warn", duration: NOTICE_DURATION_MS, link, uniformWidth: true });
      },
    });
    void poll();
    const onConnection = (event: Event) => {
      if ((event as CustomEvent<{ connected?: boolean }>).detail?.connected) void poll();
    };
    window.addEventListener("op:browser-connection", onConnection);
    return () => window.removeEventListener("op:browser-connection", onConnection);
    // `text` follows the language; one runner for the page's life is enough.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}
