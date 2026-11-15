// Always-on in the reader's words: wake outcomes, Goal states and where a
// report was sent. Shared by Agents → Always-on and the Activity panel.
import type {
  AlwaysOnChannelTarget, AlwaysOnDelivery, AlwaysOnQuietHours, AlwaysOnReportKind, AlwaysOnWakeOutcome,
} from "@/lib/agents/always-on";
import type { WakeCost, WakeHealth } from "@/lib/chat/conversation-work";
import { channelName } from "@/lib/chat/transcript/channel-speaker";

type Text = (en: string, zh: string) => string;

export function outcomeLabel(outcome: AlwaysOnWakeOutcome, text: Text): string {
  switch (outcome) {
    case "disabled": return text("Always-on was off", "常驻未开启");
    case "quiet": return text("Skipped: quiet hours", "跳过：安静时段");
    case "busy": return text("Skipped: a turn was running", "跳过：会话正在运行");
    case "goal_started": return text("Started the standing goal", "已开始常驻目标");
    case "goal_in_progress": return text("Goal in progress", "目标进行中");
    case "goal_paused": return text("Goal paused", "目标已暂停");
    case "check_in": return text("Checked in", "已巡检");
    case "idle": return text("Nothing to do", "无事可做");
    case "nothing_new": return text("Checked, nothing new", "已巡检，没有新情况");
    case "reported": return text("Checked and reported", "已巡检并报告");
    case "skipped": return text("Skipped, nothing to check", "跳过：没有要看的");
    case "budget": return text("Paused: today's budget is used", "已暂停：今天的预算已用完");
    case "interrupted": return text("Interrupted", "被中断");
    case "failed": return text("The wake failed", "唤醒失败");
    default: return String(outcome);
  }
}

const OVERDUE_AFTER_SECONDS = 600;
const FAILING_IN_A_ROW = 3;

function ago(seconds: number, text: Text): string {
  const minutes = Math.round(Math.max(0, seconds) / 60);
  if (minutes < 1) return text("just now", "刚刚");
  if (minutes < 60) return text(`${minutes} min ago`, `${minutes} 分钟前`);
  const hours = Math.round(minutes / 60);
  if (hours < 48) return text(`${hours} h ago`, `${hours} 小时前`);
  const days = Math.round(hours / 24);
  return text(`${days} days ago`, `${days} 天前`);
}

/** The one-line answer to "is this Agent alive?", from the wake log. A wake
 *  long past due means nothing is firing, whatever the log last said. */
export function aliveLabel(
  health: WakeHealth | null | undefined, nextWakeAt: number | null | undefined,
  running: boolean, now: number, text: Text,
): string {
  if (running) return text("Yes: working now", "是：正在工作");
  if (typeof nextWakeAt === "number" && now - nextWakeAt > OVERDUE_AFTER_SECONDS) {
    const due = ago(now - nextWakeAt, text);
    return text(`Looks stopped: a wake was due ${due} and has not happened`, `看起来停了：本该在${due}唤醒，但没有发生`);
  }
  if (!health) return text("No wake yet", "还没有唤醒过");
  if (health.failed_in_a_row >= FAILING_IN_A_ROW) {
    return text(`Failing: the last ${health.failed_in_a_row} wakes all failed`, `有问题：最近 ${health.failed_in_a_row} 次唤醒都失败了`);
  }
  const failed = health.failed_today
    ? text(`${health.failed_today} failed`, `${health.failed_today} 次失败`)
    : text("none failed", "没有失败");
  return text(
    `Yes: last woke ${ago(now - health.last_at, text)} · ${health.wakes_today} in 24 hours, ${failed}`,
    `是：上次唤醒在${ago(now - health.last_at, text)} · 24 小时内 ${health.wakes_today} 次，${failed}`,
  );
}

export function reportKindLabel(kind: AlwaysOnReportKind | undefined, text: Text): string {
  switch (kind) {
    case "approval": return text("Needs your decision", "需要你决定");
    case "done": return text("Finished work", "已完成的工作");
    default: return text("Findings", "发现");
  }
}

/** One report's kind, as the small label on it. */
export function reportKindTag(kind: AlwaysOnReportKind | undefined, text: Text): string {
  switch (kind) {
    case "approval": return text("Decision", "待决定");
    case "done": return text("Done", "已完成");
    default: return text("Finding", "发现");
  }
}

export function reportKindHelp(kind: AlwaysOnReportKind, text: Text): string {
  switch (kind) {
    case "finding": return text("Something new worth your attention.", "值得你关注的新情况。");
    case "approval": return text("Something only you can decide or approve.", "只有你能决定或批准的事。");
    case "done": return text("A piece of work it finished.", "它完成的一项工作。");
  }
}

/** Dollars as a short amount: 0.0042, 0.12, 3.50 with a dollar sign. */
export function usdLabel(usd: number): string {
  if (!Number.isFinite(usd)) return "—";
  if (usd > 0 && usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

/** Tokens as a short count: 950, 12.3k, 1.2M. */
function tokenCount(count: number): string {
  if (count >= 1e6) return `${(count / 1e6).toFixed(1)}M`;
  if (count >= 1e3) return `${(count / 1e3).toFixed(1)}k`;
  return String(Math.round(count));
}

/** A check-in's cost: dollars when known, and the tokens in and out. An unpriced model says the budget cannot see it. */
export function wakeCostLabel(cost: WakeCost, text: Text): string {
  const parts: string[] = [];
  if (typeof cost.usd === "number") parts.push(usdLabel(cost.usd));
  if (typeof cost.input_tokens === "number" || typeof cost.output_tokens === "number") {
    const input = tokenCount(cost.input_tokens ?? 0), output = tokenCount(cost.output_tokens ?? 0);
    parts.push(text(`${input} in, ${output} out`, `输入 ${input}，输出 ${output}`));
    if (typeof cost.usd !== "number") parts.push(text("price unknown, not counted in today's spend", "价格未知，未计入今日花费"));
  }
  return parts.join(" · ");
}

/** Today's check-in spending, against the budget when there is one. */
export function spendLabel(today: number, budget: number | null | undefined, text: Text): string {
  return typeof budget === "number"
    ? text(`${usdLabel(today)} of ${usdLabel(budget)}`, `${usdLabel(today)} / ${usdLabel(budget)}`)
    : text(`${usdLabel(today)} (no budget)`, `${usdLabel(today)}（不限预算）`);
}

export function goalStatusLabel(status: string, text: Text): string {
  const labels: Record<string, string> = {
    active: text("In progress", "进行中"),
    paused: text("Paused", "已暂停"),
    achieved: text("Achieved", "已达成"),
    blocked: text("Blocked", "受阻"),
    cancelled: text("Cancelled", "已取消"),
    cleared: text("Cleared", "已清除"),
    failed: text("Failed", "失败"),
    impossible: text("Impossible", "无法完成"),
  };
  return labels[status] ?? status;
}

/** Monday first, as wake_days counts them. */
export function weekdayNames(text: Text): string[] {
  return [
    text("Mon", "周一"), text("Tue", "周二"), text("Wed", "周三"), text("Thu", "周四"),
    text("Fri", "周五"), text("Sat", "周六"), text("Sun", "周日"),
  ];
}

/** When scheduled wakes may happen, or "" when that is always. */
export function scheduleLabel(config: { quiet_hours?: AlwaysOnQuietHours | null; wake_days?: number[] }, text: Text): string {
  const parts: string[] = [];
  const days = config.wake_days ?? [];
  if (days.length && days.length < 7) {
    const names = weekdayNames(text);
    parts.push(days.map((day) => names[day]).join(text(", ", "、")));
  }
  if (config.quiet_hours) {
    const { start, end } = config.quiet_hours;
    parts.push(text(`quiet ${start}–${end}`, `${start}–${end} 安静`));
  }
  return parts.join(" · ");
}

export function targetLabel(target: AlwaysOnChannelTarget, text: Text): string {
  return `${channelName(target.channel, text)} · ${target.label || target.peer_id}`;
}

function deliveryError(error: string | undefined, text: Text): string {
  switch (error) {
    case "auth": return text("the account is not signed in", "账号未登录或凭据失效");
    case "rate_limit": return text("rate limited", "发送过于频繁");
    case "bad_target": return text("the conversation was not found", "找不到该会话");
    case "network": return text("network error", "网络错误");
    case "not_supported": return text("not supported on this channel", "此渠道不支持");
    case "interrupted": return text("cut off by a restart", "被重启中断");
    default: return text("unknown error", "未知错误");
  }
}

export function deliveryLabel(delivery: AlwaysOnDelivery, text: Text): string {
  const where = targetLabel(delivery, text);
  if (delivery.status === "sent") return text(`Sent to ${where}`, `已发送到 ${where}`);
  if (delivery.status === "sending") return text(`Sending to ${where}…`, `正在发送到 ${where}…`);
  const reason = deliveryError(delivery.error, text);
  return text(`Not sent to ${where}: ${reason}`, `未能发送到 ${where}：${reason}`);
}
