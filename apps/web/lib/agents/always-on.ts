// Always-on as an Agent configuration (Agents → Always-on). What it does
// lives with each conversation that uses the Agent: lib/chat/conversation-work.
import { jsonFetch } from "@/lib/net/fetch-client";

/** A channel conversation (Telegram chat, Slack channel, WeChat contact…) reports go to. */
export type AlwaysOnChannelTarget = {
  channel: string;
  account_id: string;
  peer_id: string;
  label: string;
};

/** No scheduled wakes from start until end, local "HH:MM"; may cross midnight. */
export type AlwaysOnQuietHours = { start: string; end: string };

/** The kinds of report an always-on check-in makes. */
export type AlwaysOnReportKind = "finding" | "approval" | "done";
export const REPORT_KINDS: AlwaysOnReportKind[] = ["finding", "approval", "done"];

/** Where one kind of report reaches the user beyond the conversation itself. */
export type AlwaysOnNotifyRule = { desktop: boolean; channels: boolean };
export type AlwaysOnNotify = Record<AlwaysOnReportKind, AlwaysOnNotifyRule>;

export type AlwaysOnConfig = {
  enabled: boolean;
  goal: string;
  /** A fixed interval, or null: the agent schedules its own wakes (further apart, 1 to 100 hours, when it sets none). */
  wake_minutes: number | null;
  /** "provider/model" for background check-ins; "" uses the conversation's model. */
  check_in_model: string;
  notify: AlwaysOnNotify;
  /** Cap on this Agent's check-in spending per local day; null is no cap. */
  daily_budget_usd: number | null;
  research: boolean;
  autonomy: "report" | "act";
  channels: AlwaysOnChannelTarget[];
  quiet_hours: AlwaysOnQuietHours | null;
  /** Weekdays scheduled wakes may happen on; Monday is 0. */
  wake_days: number[];
};

export type AlwaysOnDelivery = AlwaysOnChannelTarget & {
  status: "sending" | "sent" | "failed";
  error?: string;
  detail?: string;
};

export type AlwaysOnWakeOutcome =
  | "disabled" | "quiet" | "busy" | "goal_started" | "goal_in_progress" | "goal_paused" | "check_in" | "idle"
  | "nothing_new" | "reported" | "skipped" | "budget" | "interrupted" | "failed";

/** The saved configuration and the channel conversations reports can go to. */
export type AlwaysOnAgentInfo = {
  agent_id: string;
  config: AlwaysOnConfig;
  channel_targets: AlwaysOnChannelTarget[];
};

export const ALL_WAKE_DAYS = [0, 1, 2, 3, 4, 5, 6];
export const NOTIFY_DEFAULTS: AlwaysOnNotify = {
  finding: { desktop: true, channels: true },
  approval: { desktop: true, channels: true },
  done: { desktop: true, channels: true },
};
export const ALWAYS_ON_DEFAULTS: AlwaysOnConfig = {
  enabled: false, goal: "", wake_minutes: null, check_in_model: "", notify: NOTIFY_DEFAULTS,
  daily_budget_usd: null, research: true, autonomy: "report", channels: [],
  quiet_hours: null, wake_days: ALL_WAKE_DAYS,
};
/** What turning a fixed interval on starts from. */
export const WAKE_MINUTES_DEFAULT = 60;

/** A saved configuration with every field present; a missing rule notifies. */
export function normalizeAlwaysOn(raw: Partial<AlwaysOnConfig> | undefined): AlwaysOnConfig {
  const config = { ...ALWAYS_ON_DEFAULTS, ...raw };
  const notify = Object.fromEntries(REPORT_KINDS.map((kind) => {
    const rule = raw?.notify?.[kind];
    return [kind, { desktop: rule?.desktop !== false, channels: rule?.channels !== false }];
  })) as AlwaysOnNotify;
  return {
    ...config,
    wake_minutes: config.wake_minutes ?? null,
    check_in_model: config.check_in_model ?? "",
    daily_budget_usd: config.daily_budget_usd ?? null,
    notify,
  };
}
/** What turning quiet hours on starts from. */
export const QUIET_HOURS_DEFAULT: AlwaysOnQuietHours = { start: "22:00", end: "08:00" };

const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

/** Quiet hours and wake days the server accepts. */
export function scheduleValid(config: Pick<AlwaysOnConfig, "quiet_hours" | "wake_days">): boolean {
  const quiet = config.quiet_hours;
  if (quiet && (!CLOCK.test(quiet.start) || !CLOCK.test(quiet.end) || quiet.start === quiet.end)) return false;
  return config.wake_days.length > 0;
}
export const ALWAYS_ON_CHANNELS_MAX = 10;
export const WAKE_MINUTES_MIN = 5;
export const WAKE_MINUTES_MAX = 10080;

export function fetchAlwaysOn(agentId: string, signal?: AbortSignal): Promise<AlwaysOnAgentInfo> {
  return jsonFetch<AlwaysOnAgentInfo>(`/api/agents/${encodeURIComponent(agentId)}/always-on`, { signal });
}

/** A wake-minutes value the server accepts; null leaves the timing to the agent. */
export function wakeMinutesValid(value: number | null): boolean {
  if (value === null) return true;
  return Number.isInteger(value) && value >= WAKE_MINUTES_MIN && value <= WAKE_MINUTES_MAX;
}

/** A daily budget the server accepts: none, or a positive amount of dollars. */
export function dailyBudgetValid(value: number | null): boolean {
  return value === null || (Number.isFinite(value) && value > 0);
}

/** The budget field's text as a value: empty is no budget. */
export function parseDailyBudget(input: string): number | null {
  const trimmed = input.trim();
  return trimmed === "" ? null : Number(trimmed);
}

/** The budget field's text for a saved value: what was typed while it still means that value, else the value. */
export function dailyBudgetText(typed: string, value: number | null): string {
  if (Object.is(parseDailyBudget(typed), value)) return typed;
  return value === null ? "" : String(value);
}

export function sameChannelTarget(a: AlwaysOnChannelTarget, b: AlwaysOnChannelTarget): boolean {
  return a.channel === b.channel && a.account_id === b.account_id && a.peer_id === b.peer_id;
}

/** Picked targets first (so one no longer listed can still be removed), then the rest. */
export function channelChoices(picked: AlwaysOnChannelTarget[], known: AlwaysOnChannelTarget[]): AlwaysOnChannelTarget[] {
  return [...picked, ...known.filter((target) => !picked.some((item) => sameChannelTarget(item, target)))];
}

export function toggleChannelTarget(picked: AlwaysOnChannelTarget[], target: AlwaysOnChannelTarget, on: boolean): AlwaysOnChannelTarget[] {
  const rest = picked.filter((item) => !sameChannelTarget(item, target));
  return on ? [...rest, target] : rest;
}
