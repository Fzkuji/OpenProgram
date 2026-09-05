/**
 * The only `window` members this app declares.
 *
 * Everything the frontend owns itself lives in a module now; what remains
 * are genuine boundaries we do not control:
 *  - `openprogramDesktop` is injected by the Electron preload script.
 *  - `__centerTabs` / `__desktopTransfer` are debug handles for the
 *    desktop multi-window acceptance runs, which drive the renderer over
 *    CDP and can only reach the stores through `window`.
 */
import type { DesktopBridge } from "./desktop-bridge";
import type { useCenterTabs } from "./state/center-tabs-store";
import type {
  acceptedTransfers,
  buildTransferPayload,
  desktopBridge,
  placementForDropIntent,
  stageIncomingTransfer,
} from "./desktop-bridge";

declare global {
  interface Window {
    /** Injected by the Electron preload; absent in a plain browser. */
    openprogramDesktop?: DesktopBridge;
    /** CDP-only debug handle. Not read by application code. */
    __centerTabs?: typeof useCenterTabs;
    /** CDP-only debug handle. Not read by application code. */
    __desktopTransfer?: {
      desktopBridge: typeof desktopBridge;
      buildTransferPayload: typeof buildTransferPayload;
      stageIncomingTransfer: typeof stageIncomingTransfer;
      placementForDropIntent: typeof placementForDropIntent;
      acceptedTransfers: typeof acceptedTransfers;
    };
  }
}

export {};
