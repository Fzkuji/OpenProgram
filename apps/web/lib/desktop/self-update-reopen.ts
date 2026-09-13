/** Report an actual loaded transcript, never a shell/document load. */
export function notifyDesktopSessionLoaded(sessionId: unknown): void {
  if (typeof window === "undefined" || typeof sessionId !== "string" ||
      !/^[A-Za-z0-9_-]{1,256}$/.test(sessionId) || window.location.pathname !== `/s/${sessionId}`) return;
  const bridge = window.openprogramDesktop;
  if (bridge?.windowId !== "main" || !bridge.selfUpdateReopen) return;
  // The main process independently checks sender, frame, route and saved intent.
  void bridge.selfUpdateReopen.sessionLoaded(sessionId).catch(() => {
    // Location ACK failure must not fail the loaded conversation. The main
    // process owns bounded retries and exposes the failure in its state.
  });
}
