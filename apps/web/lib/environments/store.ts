"use client";

/**
 * Environments — where a conversation's commands and files live (this
 * computer, an SSH host, a Docker container).
 *
 * The list comes from the worker (`list_environments`) and refreshes on
 * every `environments` broadcast. A conversation picks its environment
 * once: a draft keeps its pick in its composer settings (`environment`),
 * and the first message carries it (`environment_id` on the `chat`
 * frame). After that the conversation's own `environment_id` decides and
 * the picker locks.
 */
import { create } from "zustand";

import { wsRequest } from "@/lib/net/ws/ws-request";

export type EnvironmentKind = "local" | "ssh" | "docker";

export interface EnvironmentInfo {
  id: string;
  name: string;
  kind: EnvironmentKind;
  ssh_target?: string;
  workdir?: string;
  image?: string;
  legacy?: boolean;
}

export interface EnvironmentsSnapshot {
  default: string;
  items: EnvironmentInfo[];
}

interface EnvironmentsState extends EnvironmentsSnapshot {
  loaded: boolean;
  setSnapshot(snapshot: EnvironmentsSnapshot): void;
}

const LOCAL: EnvironmentInfo = { id: "local", name: "Local", kind: "local" };

export const useEnvironments = create<EnvironmentsState>((set) => ({
  default: "local",
  items: [LOCAL],
  loaded: false,
  setSnapshot: (snapshot) =>
    set({
      default: snapshot.default || "local",
      items: snapshot.items?.length ? snapshot.items : [LOCAL],
      loaded: true,
    }),
}));

/** Apply a list / broadcast frame from the worker. */
export function onEnvironmentsMessage(data: unknown): void {
  if (!data || typeof data !== "object") return;
  const d = data as Partial<EnvironmentsSnapshot>;
  if (!Array.isArray(d.items)) return;
  useEnvironments.getState().setSnapshot({
    default: typeof d.default === "string" ? d.default : "local",
    items: d.items as EnvironmentInfo[],
  });
}

let inflight: Promise<void> | null = null;

/** Load the list once per page (later changes arrive as broadcasts). */
export function ensureEnvironments(force = false): Promise<void> {
  if (!force && useEnvironments.getState().loaded) return Promise.resolve();
  if (inflight) return inflight;
  inflight = wsRequest<EnvironmentsSnapshot>("list_environments", {}, "environments")
    .then((data) => {
      if (data) onEnvironmentsMessage(data);
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

export function findEnvironment(
  items: EnvironmentInfo[],
  id: string | null | undefined,
): EnvironmentInfo | null {
  if (!id) return null;
  return items.find((env) => env.id === id) ?? null;
}

/** One line naming where an environment runs, for tooltips and menus. */
export function environmentWhere(env: EnvironmentInfo): string {
  if (env.kind === "ssh") return env.ssh_target + (env.workdir ? `:${env.workdir}` : "");
  if (env.kind === "docker") return env.image || "";
  return "";
}

export function environmentIconName(kind: EnvironmentKind | undefined): "monitor" | "cpu" | "box" {
  if (kind === "ssh") return "cpu";
  if (kind === "docker") return "box";
  return "monitor";
}
