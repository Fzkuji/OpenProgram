"use client";

/**
 * Machines — the computers OpenProgram reaches: this computer, SSH hosts,
 * Docker machines, virtual machines. Each has up to three connections
 * (shell, screen, browser).
 *
 * The list comes from the worker (`list_machines`) and refreshes on every
 * `machines` broadcast, a machine's connection state changing included.
 * The environment is a role on this list: the machine a conversation's
 * commands and files run on, which needs a shell. The conversation's
 * project decides it (`machine_id` on the project); the home project is
 * always this computer.
 */
import { create } from "zustand";

import { rememberMachineNames } from "@/lib/computer-use/computer-use-call";
import { wsRequest } from "@/lib/net/ws/ws-request";
import {
  LOCAL_ID,
  findKind,
  type KindInfo,
  type MachineInfo,
  type MachinesSnapshot,
} from "./machine-form";

export * from "./machine-form";

interface MachinesState {
  items: MachineInfo[];
  kinds: KindInfo[];
  loaded: boolean;
  setSnapshot(snapshot: MachinesSnapshot): void;
}

// Until the worker answers: this computer, which always has its own shell.
const LOCAL: MachineInfo = {
  id: LOCAL_ID,
  name: "This computer",
  enabled: true,
  status: { state: "connected" },
  shell: { kind: "", label: "", where: "", known: true },
};

export const useMachines = create<MachinesState>((set) => ({
  items: [LOCAL],
  kinds: [],
  loaded: false,
  setSnapshot: (snapshot) => {
    const items = snapshot.items?.length ? snapshot.items : [LOCAL];
    // Calls and rules name a machine by its id; this lets them show its name.
    rememberMachineNames(items);
    set({
      items,
      kinds: snapshot.kinds ?? [],
      loaded: true,
    });
  },
}));

/** Apply a list / broadcast frame from the worker. */
export function onMachinesMessage(data: unknown): void {
  if (!data || typeof data !== "object") return;
  const d = data as Partial<MachinesSnapshot>;
  if (!Array.isArray(d.items)) return;
  // A list arrived (an answer or a broadcast): a quiet retry is no longer needed.
  stopRetrying();
  useMachines.getState().setSnapshot({
    items: d.items as MachineInfo[],
    kinds: Array.isArray(d.kinds) ? (d.kinds as KindInfo[]) : [],
  });
}

const RETRY_FIRST_MS = 1_000;
const RETRY_MAX_MS = 30_000;

let inflight: Promise<void> | null = null;
let failures = 0;
let retry: ReturnType<typeof setTimeout> | undefined;

function stopRetrying(): void {
  clearTimeout(retry);
  retry = undefined;
  failures = 0;
}

/** Load the list once per page (later changes arrive as broadcasts); `force`
 *  reads it again. The list shown stays as it is until an answer arrives. No
 *  answer (socket down, timed out) is tried again quietly, 1 s, 2 s, 4 s … up
 *  to 30 s, until one arrives. An answer the worker refuses or that holds no
 *  list is a fact, not a load problem: it is not asked again. */
export function ensureMachines(force = false): Promise<void> {
  if (!force && useMachines.getState().loaded) return Promise.resolve();
  if (inflight) return inflight;
  clearTimeout(retry);
  retry = undefined;
  inflight = wsRequest<MachinesSnapshot & { status?: unknown }>("list_machines", {}, "machines")
    .then((data) => {
      if (data === null) {
        failures += 1;
        const wait = Math.min(RETRY_FIRST_MS * 2 ** (failures - 1), RETRY_MAX_MS);
        console.warn(`Machines could not be read; trying again quietly in ${wait} ms`);
        retry = setTimeout(() => {
          retry = undefined;
          void ensureMachines(true);
        }, wait);
        return;
      }
      stopRetrying();
      if (data.status === "error" || !Array.isArray(data.items)) {
        console.warn("The worker refused the machine list; keeping the one shown", data);
        return;
      }
      onMachinesMessage(data);
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** Tests start from nothing. */
export function resetMachinesForTests(): void {
  stopRetrying();
  inflight = null;
}

export function findMachine(items: MachineInfo[], id: string | null | undefined): MachineInfo | null {
  if (!id) return null;
  return items.find((machine) => machine.id === id) ?? null;
}

/** The machine a project is on: this computer for the home project and for a project that names none. */
export function projectMachineId(project: { is_default?: boolean; machine_id?: string | null } | null | undefined): string {
  if (!project || project.is_default) return LOCAL_ID;
  return project.machine_id || LOCAL_ID;
}

/** The machine answered its last check (this computer always does). */
export function machineOnline(machine: MachineInfo | null | undefined): boolean {
  if (!machine) return false;
  if (machine.id === LOCAL_ID) return true;
  return machine.status?.state === "connected";
}

/** A project folder on this machine is on the machine's own disk, chosen there (an SSH host), not on this computer's. */
export function machineKeepsFiles(machine: MachineInfo | null | undefined, kinds: KindInfo[]): boolean {
  if (!machine || machine.id === LOCAL_ID || !machine.shell) return false;
  return findKind(kinds, "shell", machine.shell.kind)?.remote_files === true;
}

/** The project's folder is on this computer's disk: the home project, a
 *  project on this computer, or one on a machine that mounts this
 *  computer's folders (Docker). False for a machine that keeps its own
 *  files (an SSH host) and for a machine no longer configured. Git, the
 *  file manager and worktrees work only on such a folder. */
export function projectFolderHere(
  project: { is_default?: boolean; machine_id?: string | null } | null | undefined,
  items: MachineInfo[],
  kinds: KindInfo[],
): boolean {
  const id = projectMachineId(project);
  if (id === LOCAL_ID) return true;
  const machine = findMachine(items, id);
  return !!machine && !machineKeepsFiles(machine, kinds);
}

/** This computer, a machine each conversation gets its own instance of, or any other machine. */
export function machineIconName(machine: MachineInfo | null | undefined, kinds: KindInfo[]): "monitor" | "server-2" | "box" {
  // This computer, or another computer's own OpenProgram.
  if (!machine || machine.id === LOCAL_ID || machine.openprogram) return "monitor";
  const shell = machine.shell ? findKind(kinds, "shell", machine.shell.kind) : null;
  return shell?.instance_scope === "conversation" ? "box" : "server-2";
}
