"use client";

/** An agent as a row in Activity: a helper a scheduled run started (avatar,
 *  name, status badge, what its subtree cost), or a message the conversation
 *  sent that is still unanswered. */
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { HoverTip } from "@/components/ui/tooltip";
import { fmtUsd } from "@/components/settings/providers/status/model";
import { formatTokens } from "@/lib/chat/context-occupancy";
import type { RosterStatus } from "@/lib/execution/agent-roster";
import { subAgentShape } from "@/lib/runtime-bridge/dag/render/agent-look";
import { AgentGlyph, Status, type Text } from "./running-rows";
import styles from "./running-panel.module.css";

/** One status set for every agent row. Waiting on the reader and stalled
 *  are the outlined badge with the yellow dot; at rest it is muted. */
/** The words of each status, the same wherever an agent's state is named. */
export function rosterBadgeWords(status: RosterStatus, text: Text): string {
  switch (status) {
    case "running": return text("Running", "正在运行");
    case "approval": return text("Needs approval", "等你授权");
    case "input": return text("Needs input", "等你回答");
    case "stalled": return text("Stalled", "无进展");
    case "failed": return text("Failed", "失败");
    case "waiting": return text("Waiting", "等待中");
    default: return text("Idle", "空闲");
  }
}

export function RosterBadge({ status, text }: { status: RosterStatus; text: Text }) {
  const words = rosterBadgeWords(status, text);
  switch (status) {
    case "running": return <Badge variant="secondary"><span data-icon="inline-start" className="indicator-dot sm --neutral pulse-opacity" aria-hidden="true" />{words}</Badge>;
    case "approval": case "input": case "stalled":
      return <Badge variant="outline"><span data-icon="inline-start" className="indicator-dot sm --warn" aria-hidden="true" />{words}</Badge>;
    case "failed": return <Badge variant="destructive">{words}</Badge>;
    default: return <Badge variant="outline" className="text-text-muted">{words}</Badge>;
  }
}

/** In Activity a helper that waits on the reader says so with a dot and
 *  the words, the way the rows' status lines do, not a pill. */
const waitsOnReader = (status: RosterStatus) => status === "approval" || status === "input" || status === "stalled";

/** "18.4k tokens · 12 tool calls", with whatever of the two is known. */
export function usageTip(facts: { usage: { tokens?: number | null; tool_calls?: number | null } | null } | null | undefined, text: Text): string {
  const usage = facts?.usage;
  if (!usage) return "";
  const calls = usage.tool_calls;
  return [
    usage.tokens != null ? text(`${formatTokens(usage.tokens)} tokens`, `${formatTokens(usage.tokens)} tokens`) : "",
    calls != null ? text(`${calls} tool call${calls === 1 ? "" : "s"}`, `${calls} 次工具调用`) : "",
  ].filter(Boolean).join(" · ");
}

export interface RosterRowProps {
  name: string;
  status: RosterStatus;
  /** Sits this far in: 16 for each level under the agent. */
  indent: number;
  /** Dollars; null when a price is unknown or nothing was spent yet. */
  cost?: number | null;
  /** Shown on hover: tokens and tool calls. */
  tip?: string;
  /** A message sent to another agent: "→ Name". */
  outgoing?: boolean;
  onOpen: () => void;
}

export function RosterRow({ name, status, indent, cost = null, tip = "", outgoing = false, onOpen, text }: RosterRowProps & { text: Text }) {
  const row = <Button variant="ghost" className={styles.agentRow} style={{ ["--indent" as string]: `${indent}px` }}
    data-outgoing={outgoing ? "1" : undefined} data-roster-row="" onClick={onOpen}
    aria-label={outgoing ? `${text(`Message sent to ${name}`, `发给 ${name} 的消息`)}, ${rosterBadgeWords(status, text)}` : undefined}>
    <AgentGlyph shape={subAgentShape(name)} />
    {/* One line that gives up the cost first: when name, badge and cost do
        not fit, the cost wraps out of sight and the name keeps the room. */}
    <span className="flex h-(--ui-control-inline) min-w-0 flex-[1_1_0] flex-wrap items-center gap-x-[8px] overflow-hidden">
      <span className="flex h-(--ui-control-inline) max-w-full min-w-0 flex-auto items-center gap-[8px]">
        <span className={`${styles.name} min-w-0 flex-initial`}>{outgoing ? <span className={styles.arrow} aria-hidden="true">→ </span> : null}{name}</span>
        {waitsOnReader(status) ? <span className="flex-none"><Status tone="warn">{rosterBadgeWords(status, text)}</Status></span> : <RosterBadge status={status} text={text} />}
      </span>
      {cost !== null ? <span className={styles.cost}>{fmtUsd(cost)}</span> : null}
    </span>
  </Button>;
  // The cost is on the tip too, since a narrow panel drops it from the row.
  const label = [cost !== null ? fmtUsd(cost) : "", tip].filter(Boolean).join(" · ");
  return label ? <HoverTip label={label} side="left">{row}</HoverTip> : row;
}
