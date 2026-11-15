"use client";

import { create } from "zustand";
import { followAppTarget, TERMINAL_TARGET } from "../chat/resources/session-resources.ts";

// Advisory shell reports; command exit never proves an external effect or task success.
export type TerminalShellIntegration = {
  status: "starting" | "ready" | "unavailable" | "ended";
  mode: "zsh-hooks" | "bash-debug" | null; reason: string | null;
  event_sequence: number;
  cwd: { value: string; event_sequence: number } | null;
  cycle: { id: number; phase: "running" | "finished" | "unknown"; exit_code: number | null } | null;
};
export type TerminalResource = {
  terminal_id: string; generation: string; preset: string; status: string;
  start_cwd: string; pid: number | null; exit_code: number | null;
  shell_integration?: TerminalShellIntegration;
  output_end: number; shared: boolean; session_ids: string[];
  input_revision: number; human_input_pending: boolean; in_use: boolean;
};
export type TerminalReply = Partial<TerminalResource> & {
  ok: boolean; error?: string; items?: TerminalResource[]; data?: string;
  next_cursor?: number; has_more?: boolean; truncated?: boolean;
};
export type TerminalResourceApi = {
  windowId: string;
  resource: (action: string, request?: Record<string, unknown>) => Promise<TerminalReply>;
  agentTicket: (ticket: string) => Promise<TerminalReply>;
  onResource: (callback: (resource: TerminalResource) => void) => () => void;
};
declare global { interface Window { openprogramTerminals?: TerminalResourceApi } }
export function terminalResourceApi(): TerminalResourceApi | undefined {
  return typeof window === "undefined" ? undefined : window.openprogramTerminals;
}
export const useTerminalResources = create<{
  rows: Record<string, TerminalResource>; available: boolean; error: string;
}>(() => ({ rows: {}, available: false, error: "" }));

function ingest(resource: TerminalResource): void {
  if (!resource || typeof resource.terminal_id !== "string" || typeof resource.generation !== "string"
    || !Array.isArray(resource.session_ids)) return;
  const previous = useTerminalResources.getState().rows[resource.terminal_id];
  useTerminalResources.setState(state => ({ rows: { ...state.rows, [resource.terminal_id]: resource } }));
  // The Agent took the terminal or typed into it: its conversations' chat
  // previews follow it, the way they follow Pages and app windows.
  if (resource.in_use && resource.status === "running"
      && (!previous?.in_use || previous.input_revision !== resource.input_revision)) {
    for (const sessionId of resource.session_ids) {
      followAppTarget(sessionId, `${TERMINAL_TARGET}${resource.terminal_id}`);
    }
  }
}
let installed: TerminalResourceApi | undefined;
export function installTerminalResourceBridge(): void {
  const api = terminalResourceApi();
  if (!api || installed === api) return;
  installed = api;
  useTerminalResources.setState({ available: true });
  api.onResource(ingest);
  const before = useTerminalResources.getState().rows;
  void api.resource("list").then(result => {
    if (!result.ok) throw Error(result.error || "terminal_list_unavailable");
    const current = useTerminalResources.getState().rows;
    for (const resource of result.items || []) {
      if (current[resource.terminal_id] === before[resource.terminal_id]) ingest(resource);
    }
  }).catch(() => useTerminalResources.setState({ error: "terminal_list_unavailable" }));
  window.addEventListener("op:ws-message", (event: Event) => {
    const message = (event as CustomEvent).detail;
    const request = message?.data;
    if (message?.type !== "terminal.command"
      || request?.window_id !== api.windowId
      || typeof request.ticket !== "string" || !/^[0-9a-f]{64}$/.test(request.ticket)) return;
    // Native host redeems an opaque worker ticket, not renderer launch arguments.
    // Native completion goes directly to the worker; never replay on reconnect.
    void api.agentTicket(request.ticket).catch(() => ({ ok: false, error: "terminal_result_unconfirmed" } as TerminalReply))
      .then(result => {
        if (result.terminal_id && result.generation && result.session_ids) ingest(result as TerminalResource);
      });
  });
}
