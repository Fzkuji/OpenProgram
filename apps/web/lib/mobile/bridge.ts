/**
 * The phone app (apps/mobile) around this UI.
 *
 * The app serves these files at ``openprogram://app`` and forwards HTTP
 * to the paired computer itself, so fetches stay relative. Only the
 * WebSocket goes straight to the computer: the app names the address and
 * the subprotocols, the credential among them (a browser WebSocket cannot
 * set headers). Calls back to the app go through one script message
 * handler.
 */
export interface MobileComputer {
  name: string;
  /** How it is reached right now: "lan" (Same Wi-Fi) or "anywhere". */
  via: string;
}

export interface MobileBridge {
  socket: { url: string; protocols: string[] };
  computer: MobileComputer;
}

type Native = { webkit?: { messageHandlers?: { openprogram?: { postMessage(message: unknown): void } } } };

export function mobileBridge(): MobileBridge | null {
  if (typeof window === "undefined") return null;
  return window.openprogramMobile ?? null;
}

/** Ask the app to do something native: scan a code, open the computers list, sign this computer out. */
export function callMobile(action: "signedOut" | "computers" | "haptic", detail: Record<string, unknown> = {}): void {
  (window as unknown as Native).webkit?.messageHandlers?.openprogram?.postMessage({ action, ...detail });
}

/** A tap of the phone's haptic engine, in the app only, and only for
 *  the person's own actions (Happy's rule): `light` for a send or a fold,
 *  `medium` when a long press or a swipe arms, `selection` for a picker or
 *  a switch, `success` when something is done (archived). */
export function haptic(style: "light" | "medium" | "selection" | "success" = "light"): void {
  if (mobileBridge()) callMobile("haptic", { style });
}
