import { useSyncExternalStore } from "react";

import { desktopBridge, type DesktopBridge } from "./desktop-bridge";

const never = () => () => {};
const noBridge = () => null;

/** The desktop bridge, known on the first client render. Reading it in an
 *  effect made every desktop-only section appear a frame after the page. */
export function useDesktopBridge(): DesktopBridge | null {
  return useSyncExternalStore(never, desktopBridge, noBridge);
}
