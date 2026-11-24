import styles from "../../../components/settings/settings-page.module.css";

/**
 * Suspense fallback for /settings/<tab>, shown only while the tab's chunk
 * is still on its way: the settings page's empty surface, with no title,
 * no "Loading…" and no skeleton. Every settings tab is prefetched once the
 * first page has painted (components/app-shell.tsx), so a tab click
 * normally finds its chunk loaded and this never shows.
 */
export default function SettingsLoading() {
  return <div className={styles.page} data-route-pending="" />;
}
