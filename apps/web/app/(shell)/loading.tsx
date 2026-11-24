import styles from "./shell-loading.module.css";

/**
 * Suspense fallback for every (shell) route without its own loading.tsx,
 * shown only while a route's chunk is still on its way. It is the page's
 * empty background and nothing else: no title, no "Loading…", no
 * skeleton. AppShell prefetches every route once the first page has
 * painted (`warmRoutes` in components/app-shell.tsx), so a click normally
 * finds its route already loaded and this never shows.
 */
export default function ShellLoading() {
  return <div className={styles.wrap} data-route-pending="" />;
}
