/**
 * What the Devices page asks the worker: machine changes over the
 * WebSocket (answered with ``machine_result`` for that request), and the
 * paired phones and browsers over ``/api/auth/devices``.
 */
import { jsonFetch } from "@/lib/net/fetch-client";
import { wsRequest } from "@/lib/net/ws/ws-request";

export type Text = (en: string, zh: string) => string;

export interface MachineResult {
  ok: boolean;
  error?: string;
  id?: string;
}

export interface PairedDevice {
  id: string;
  label: string;
  created_at: number;
  last_used_at: number;
}

// A save may wait on the kind's one round trip to its address (an OSWorld server takes up to 35 s);
// the other changes only write settings.
export const SAVE_WAIT_MS = 60000;
export const CHANGE_WAIT_MS = 8000;
// Test checks each connection in turn, each bounded by its own round trip.
export const TEST_WAIT_MS = 90000;

/** A machine change, correlated by request id: a late answer never settles another change. */
export async function machineRequest(
  action: string,
  payload: Record<string, unknown>,
  text: Text,
  timeoutMs = CHANGE_WAIT_MS,
): Promise<MachineResult> {
  const noAnswer = { ok: false, error: text("The worker did not answer.", "后台没有响应。") };
  try {
    return (await wsRequest<MachineResult>(action, payload, "machine_result", { requestId: true }, timeoutMs)) ?? noAnswer;
  } catch {
    return noAnswer;
  }
}

export async function readPairedDevices(): Promise<{ devices: PairedDevice[]; current: string | null }> {
  return jsonFetch<{ devices: PairedDevice[]; current: string | null }>("/api/auth/devices");
}

export async function sshHosts(): Promise<string[]> {
  const res = await wsRequest<{ hosts: string[] }>("list_ssh_hosts", {}, "ssh_hosts", { requestId: true }, CHANGE_WAIT_MS)
    .catch(() => null);
  return res?.hosts ?? [];
}
