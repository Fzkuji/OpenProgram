"use client";

import { usePathname } from "next/navigation";
import { useTranslation } from "@/lib/i18n";
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
const TAB_KEYS: Record<string, "settings.tab.providers"|"settings.tab.search"|"settings.tab.channels"|"settings.tab.general"> = {
  providers: "settings.tab.providers",
  search: "settings.tab.search",
  channels: "settings.tab.channels",
  general: "settings.tab.general",
};

export default function SettingsLoading() {
  const { t } = useTranslation();
  const pathname = usePathname() || "";
  const tab = pathname.split("/")[2] || "providers";
  const key = TAB_KEYS[tab];
  const title = key ? t(key) : t("settings.title");

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <h2 className={styles.pageTitle}>{title}</h2>
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
