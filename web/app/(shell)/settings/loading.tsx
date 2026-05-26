"use client";

import { usePathname } from "next/navigation";
import styles from "@/components/settings/settings-page.module.css";

/**
 * Suspense fallback for /settings/<tab>. Renders immediately when the
 * router transitions, before the next page.tsx's chunk is fetched +
 * compiled. The OLD page is unmounted right away — no more "click,
 * nothing happens for a second, snap to new page" jank.
 *
 * Two cheap UX wins layered on top of the generic skeleton:
 *
 * 1. The real page TITLE is rendered synchronously (no JS work — it's
 *    just a string keyed off pathname). The user sees "Channels"
 *    appear the instant they click the Channels tab, instead of a
 *    grey rectangle. The body is still a skeleton until the page
 *    chunk lands.
 *
 * 2. The skeleton shapes mirror the real `.pageHeader` + `.pageBody`
 *    layout, so when the actual page replaces the fallback there's
 *    no width/height shift.
 */
const TAB_META: Record<string, { title: string }> = {
  providers: { title: "LLM Providers" },
  search: { title: "Web Search" },
  channels: { title: "Channels" },
  general: { title: "General" },
};

export default function SettingsLoading() {
  const pathname = usePathname() || "";
  const tab = pathname.split("/")[2] || "providers";
  const meta = TAB_META[tab] || { title: "Settings" };

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <h2 className={styles.pageTitle}>{meta.title}</h2>
        <div className={styles.skelMeta} />
      </div>
      <div className={styles.pageBody}>
        <div className={styles.skelBlock} />
        <div className={styles.skelBlock} />
        <div className={styles.skelBlockShort} />
      </div>
    </div>
  );
}
