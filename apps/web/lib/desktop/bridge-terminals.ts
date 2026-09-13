// Desktop bridge terminals responsibilities.
import "@/lib/net/ws-events";
import { type DesktopBridge } from "./bridge-api";



export function desktopTerminalId(
  bridge: Pick<DesktopBridge, "windowId">,
  preset: "shell" | "claude",
): string {
  return `terminal:${bridge.windowId}:${preset}`;
}

/** Terminal processes follow tab lifetime, not component lifetime. Switching
 *  tabs unmounts inactive panes, so stopping from TerminalPage cleanup would
 *  terminate a still-open terminal. Store reconciliation stops only the two
 *  fixed presets whose built-in tab has actually been closed. */
export function destroyStaleTerminals(
  bridge: DesktopBridge,
  tabs: ReadonlyArray<{ kind: string; page?: string }>,
): void {
  if (!bridge.terminal) return;
  for (const preset of ["shell", "claude"] as const) {
    const page = preset === "shell" ? "terminal" : "claude";
    const alive = tabs.some((tab) => tab.kind === "builtin" && tab.page === page);
    if (!alive) bridge.terminal.stop(desktopTerminalId(bridge, preset));
  }
}
