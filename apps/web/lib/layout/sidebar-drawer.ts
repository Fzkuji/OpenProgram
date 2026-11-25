/**
 * Imperative handle on the left sidebar's open state, for the phone
 * layout's header button, scrim and edge swipe, which do not own the
 * sidebar. `Sidebar` registers the implementation on mount; every call is
 * a no-op before that.
 */

export interface SidebarDrawerApi {
  isOpen: () => boolean;
  /** Opens or closes without storing the choice (the phone drawer). */
  setOpen: (open: boolean) => void;
}

let api: SidebarDrawerApi | null = null;

export function setSidebarDrawerApi(next: SidebarDrawerApi | null): void {
  api = next;
}

export const sidebarDrawer = {
  isOpen: (): boolean => api?.isOpen() ?? false,
  open: (): void => api?.setOpen(true),
  close: (): void => api?.setOpen(false),
  toggle: (): void => api?.setOpen(!(api?.isOpen() ?? false)),
};
