/** Shallow SPA navigation for paths that have no exported route.
 *
 * The static export (single-port architecture) only contains the listed
 * pages — /s/<id>, /skills/<name>, /settings/providers/<id>, /plugin/…
 * are served by the worker's SPA fallback and resolved client-side from
 * the pathname. `router.push` to such a path makes the Next router miss
 * the route tree and fall back to an MPA hard navigation (full reload).
 * Native history.pushState avoids that: Next 14.1+ keeps usePathname in
 * sync with it, so every pathname-derived view updates in place.
 */
export function pushPath(path: string): void {
  // Duplicate pushes (click handler + tab-activation effect racing on stale
  // pathname) would stack identical history entries and eat one Back press.
  if (window.location.pathname === path) return;
  window.history.pushState(null, "", path);
}
