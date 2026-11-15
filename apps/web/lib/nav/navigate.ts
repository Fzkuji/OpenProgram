/**
 * Client-side navigation for non-React callers.
 *
 * `AppShell` owns the Next.js router, so it registers `router.push` here
 * on mount. Plain modules (desktop bridge, runtime-bridge handlers) then
 * call `navigate()` instead of `window.location.href = ...`, which would
 * full-reload and tear down the shell.
 *
 * Falls back to a hard location assignment when the shell has not mounted
 * yet, so an early call still lands on the right route.
 */

type NavigateFn = (path: string) => void;

let navigateImpl: NavigateFn | null = null;

/** Called by AppShell with the Next.js router's push. */
export function setNavigate(fn: NavigateFn | null): void {
  navigateImpl = fn;
}

/** True once AppShell has registered the router. */
export function hasNavigate(): boolean {
  return navigateImpl !== null;
}

export function navigate(path: string): void {
  if (navigateImpl) {
    navigateImpl(path);
    return;
  }
  if (typeof window !== "undefined") window.location.href = path;
}
