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

function capacityValue(value: { used: number; limit: number | null }): string {
  return `${value.used}/${value.limit ?? "∞"}`;
}

export function queueResourceSummary(
  resource: JobResourceView | undefined,
): string | null {
  if (!resource || resource.status !== "queued") return null;
  const { capacity } = resource;
  const position = capacity.queue_position == null
    ? "?"
    : String(capacity.queue_position);
  return [
    `Queue #${position}`,
    `Session ${capacityValue(capacity.session_live)} live`,
    `${capacityValue(capacity.session_queued)} queued`,
    `${capacityValue(capacity.session_jobs)} jobs`,
    `Scheduler ${capacity.scheduler_capacity}`,
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
  if (!resource) return [];
  if (resource.resource_state === "legacy/unmetered") {
    const legacy: JobResourceDetail[] = [
      { key: "state", value: "Legacy / unmetered" },
    ];
    if (resource.reason_code) {
      legacy.push({ key: "reason", value: resource.reason_code });
    }
    return legacy;
  }
  const { budget } = resource;
  const unknownEvents = Math.max(
    budget.cost_usd.unknown_events ?? 0,
    budget.shared_remaining.cost_unknown_events ?? 0,
  );
  let cost: string;
  if (budget.cost_usd.known !== true || unknownEvents > 0) {
    cost = `Unknown cost${unknownEvents ? ` (${unknownEvents} events)` : ""}`;
  } else {
    cost = costRemaining(
      localCostRemaining(
        budget.cost_usd.limit,
        budget.cost_usd.actual,
        budget.cost_usd.reserved,
      ),
      microUsd(budget.shared_remaining.cost_usd),
    );
  }
  const details: JobResourceDetail[] = [
    { key: "state", value: resource.resource_state },
    {
      key: "tokens",
      value: remaining(
        remainingValue(
          budget.tokens.limit,
          budget.tokens.actual,
          budget.tokens.reserved,
        ),
        budget.shared_remaining.tokens,
      ),
    },
    { key: "cost", value: cost },
    {
      key: "runtime",
      value: secondsRemaining(
        budget.runtime_seconds.limit, budget.runtime_seconds.used,
      ),
    },
    {
      key: "idle",
      value: secondsRemaining(
        budget.idle_seconds.limit, budget.idle_seconds.used,
      ),
    },
  ];
  if (resource.reason_code) {
    details.push({ key: "reason", value: resource.reason_code });
  }
  return details;
}
