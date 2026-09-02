import type { JobResourceView } from "./net/ws-events";

export type JobResourceDetail = {
  key: "state" | "tokens" | "cost" | "runtime" | "idle" | "reason";
  value: string;
};

export function canonicalExecutionId(
  resource: JobResourceView | undefined,
): string | undefined {
  return resource?.execution_id
    ?? (resource?.execution?.execution_id as string | undefined);
}

export function nextReplaySequence(resource: JobResourceView | undefined): number | undefined {
  return resource?.event_cursor?.next_sequence;
}

export function queueResourceSummary(
  resource: JobResourceView | undefined,
): string | null {
  const canonical = resource?.resource;
  if (!resource || !canonical || resource.status !== "queued") return null;
  const position = canonical.queue_wait?.position == null
    ? "?"
    : String(canonical.queue_wait.position);
  return [
    `Queue #${position}`,
    canonical.queue_wait?.state || "queued",
  ].join(" · ");
}

function remainingValue(
  limit: number | null,
  ...used: Array<number | null>
): number | null | undefined {
  if (limit == null) return null;
  if (used.some((value) => value == null)) return undefined;
  return Math.max(0, limit - used.reduce<number>(
    (total, value) => total + (value ?? 0), 0,
  ));
}

function remaining(...values: Array<number | null | undefined>): string {
  if (values.some((value) => value === undefined)) return "Unknown";
  const bounded = values.filter((value): value is number => value != null);
  return bounded.length ? String(Math.min(...bounded)) : "Unlimited";
}

function secondsRemaining(limit: number | null, used: number | null): string {
  const value = remaining(remainingValue(limit, used));
  return value === "Unlimited" || value === "Unknown" ? value : `${value}s`;
}

function microUsd(value: string | null): bigint | null | undefined {
  if (value == null) return null;
  const match = /^(\d+)(?:\.(\d{1,6}))?$/.exec(value);
  if (!match) return undefined;
  return BigInt(match[1]) * BigInt(1_000_000)
    + BigInt((match[2] || "").padEnd(6, "0"));
}

function localCostRemaining(
  limit: string | null,
  actual: string | null,
  reserved: string | null,
): bigint | null | undefined {
  if (limit == null) return null;
  const values = [limit, actual, reserved].map(microUsd);
  if (values.some((value) => value == null)) return undefined;
  let value = (values[0] ?? BigInt(0))
    - (values[1] ?? BigInt(0))
    - (values[2] ?? BigInt(0));
  if (value < BigInt(0)) value = BigInt(0);
  return value;
}

function costRemaining(
  ...values: Array<bigint | null | undefined>
): string {
  if (values.some((value) => value === undefined)) return "Unknown";
  const bounded = values.filter((value): value is bigint => value != null);
  if (!bounded.length) return "Unlimited";
  const value = bounded.reduce((least, current) => (
    current < least ? current : least
  ));
  const whole = value / BigInt(1_000_000);
  const fraction = String(value % BigInt(1_000_000))
    .padStart(6, "0")
    .replace(/0+$/, "")
    .padEnd(2, "0");
  return `$${whole}.${fraction}`;
}

export function jobResourceDetails(
  resource: JobResourceView | undefined,
): JobResourceDetail[] {
  const canonical = resource?.resource;
  if (!resource || !canonical) return [];
  if (canonical.resource_state === "legacy/unmetered") {
    const legacy: JobResourceDetail[] = [
      { key: "state", value: "Legacy / unmetered" },
    ];
    const reason = resource.execution?.reason_code;
    if (typeof reason === "string" && reason) {
      legacy.push({ key: "reason", value: reason });
    }
    return legacy;
  }
  const usage = canonical.usage;
  const unknownEvents = Math.max(
    usage.cost_usd?.unknown_events ?? 0,
    usage.shared_remaining?.cost_unknown_events ?? 0,
  );
  let cost: string;
  if (usage.cost_usd?.known !== true || unknownEvents > 0) {
    cost = `Unknown cost${unknownEvents ? ` (${unknownEvents} events)` : ""}`;
  } else {
    cost = costRemaining(
      localCostRemaining(
        usage.cost_usd?.limit ?? null,
        usage.cost_usd?.actual ?? null,
        usage.cost_usd?.reserved ?? null,
      ),
      microUsd(usage.shared_remaining?.cost_usd ?? null),
    );
  }
  const details: JobResourceDetail[] = [
    { key: "state", value: canonical.resource_state },
    {
      key: "tokens",
      value: remaining(
        remainingValue(
          usage.tokens?.limit ?? null,
          usage.tokens?.actual ?? null,
          usage.tokens?.reserved ?? null,
        ),
        usage.shared_remaining?.tokens,
      ),
    },
    { key: "cost", value: cost },
    {
      key: "runtime",
      value: secondsRemaining(
        usage.runtime_seconds?.limit ?? null,
        usage.runtime_seconds?.used ?? null,
      ),
    },
    {
      key: "idle",
      value: secondsRemaining(
        usage.idle_seconds?.limit ?? null,
        usage.idle_seconds?.used ?? null,
      ),
    },
  ];
  const reason = resource.execution?.reason_code;
  if (typeof reason === "string" && reason) {
    details.push({ key: "reason", value: reason });
  }
  return details;
}
