import { useEffect, useState } from "react";

import type { DesktopUpdateState } from "./desktop-bridge";
import { useDesktopBridge } from "./use-desktop-bridge";

/**
 * The desktop App's update state (main/self-update/update-service.js), kept
 * current by the state the main process pushes. Null in the browser and until
 * the first answer.
 */
export function useDesktopUpdateState(): [DesktopUpdateState | null, (state: DesktopUpdateState) => void] {
  const bridge = useDesktopBridge();
  const [state, setState] = useState<DesktopUpdateState | null>(null);
  useEffect(() => {
    const updates = bridge?.updates;
    if (!updates) return;
    let active = true;
    void updates.getState().then((next) => {
      if (active && next) setState(next);
    }).catch((error: unknown) => {
      // The state pushed through onState fills in what this read missed.
      console.warn("The update state could not be read", error);
    });
    const unsubscribe = updates.onState((next) => {
      if (active) setState(next);
    });
    return () => {
      active = false;
      unsubscribe();
    };
  }, [bridge]);
  return [state, setState];
}
