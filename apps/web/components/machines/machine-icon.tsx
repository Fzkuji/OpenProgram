"use client";

import type { CSSProperties } from "react";

import { SolarIcon } from "@/components/solar-icons";
import { useTranslation } from "@/lib/i18n";
import {
  LOCAL_ID,
  machineIconName,
  type KindInfo,
  type MachineInfo,
  type MachineState,
} from "@/lib/machines/store";
import { cn } from "@/lib/utils";

/** The machine's icon (the one Settings > Machines shows) with its
 *  connection state as a small dot at the bottom right: green connected,
 *  grey switched off, a turning ring while the first check runs, red when
 *  it could not be reached. This computer carries no dot: it is always here.
 *  Used by the sidebar's project headers, the project picker and the
 *  composer's machine chip; the composer's own chip and menu pass `fill`,
 *  the composer's icon rule. */
export function MachineIcon({
  machine,
  kinds,
  size = 16,
  fill = false,
  className,
  style,
}: {
  machine: MachineInfo | null | undefined;
  kinds: KindInfo[];
  size?: number;
  /** Fill the icon box, as the composer's icons do. */
  fill?: boolean;
  className?: string;
  style?: CSSProperties;
}) {
  const state: MachineState | null =
    machine && machine.id !== LOCAL_ID ? machine.status?.state ?? "connecting" : null;
  return (
    <span className={cn("relative inline-flex shrink-0", className)} style={style} aria-hidden="true">
      <SolarIcon name={machineIconName(machine, kinds)} size={size} fit={fill ? "fill" : undefined} />
      {state ? <StateDot state={state} /> : null}
    </span>
  );
}

const DOT_COLOR: Record<Exclude<MachineState, "connecting">, string> = {
  connected: "var(--status-working)",
  disconnected: "var(--text-muted)",
  error: "var(--status-failed)",
};

function StateDot({ state }: { state: MachineState }) {
  if (state === "connecting") {
    return (
      <svg
        className="absolute -bottom-[1px] -right-[1px] block size-[7px] animate-spin"
        viewBox="0 0 8 8"
        fill="none"
        data-machine-state={state}
      >
        <circle cx="4" cy="4" r="3" stroke="var(--text-muted)" strokeOpacity={0.3} strokeWidth={2} />
        <circle
          cx="4" cy="4" r="3" stroke="var(--text-muted)" strokeWidth={2} strokeLinecap="round"
          pathLength={100} strokeDasharray="30 70"
        />
      </svg>
    );
  }
  return (
    <span
      className="absolute -bottom-[1px] -right-[1px] block size-[6px] rounded-full"
      style={{ backgroundColor: DOT_COLOR[state] }}
      data-machine-state={state}
    />
  );
}

/** The machine's name as people read it: "This computer" for this one. */
export function useMachineName(): (machine: MachineInfo | null | undefined, fallback?: string) => string {
  const { text } = useTranslation();
  return (machine, fallback = "") =>
    !machine ? fallback : machine.id === LOCAL_ID ? text("This computer", "这台电脑") : machine.name;
}
