"use client";

import styles from "@/components/settings/settings-page.module.css";

/**
 * Suspense fallback shown while a /settings/<tab>'s page chunk is
 * loading. In dev mode that includes the on-demand Next.js compile —
 * which can take 0.5–2s for a route that hasn't been visited yet —
 * so without this file React keeps the OLD page on screen for the
 * whole compile, making the click feel unresponsive.
 *
 * With this file: click → old section unmounts immediately → skeleton
 * appears in the body slot → real page replaces it when ready. The
 * settings tabs layout (topbar + nav column) keeps rendering above
 * because it's the parent layout, not part of the fallback tree.
 *
 * The skeleton intentionally mirrors the real page's `.pageHeader`
 * shape so there's no layout-shift when the actual content lands.
 */
export default function SettingsLoading() {
  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.skelTitle} />
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
