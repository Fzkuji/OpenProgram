"use client";

/** The rows under an agent in the Activity overview: one per helper it
 *  started (avatar, name, status badge, what its subtree cost), one per
 *  message it sent that is still unanswered, and the fold that holds the
 *  helpers that finished. */
import type { KeyboardEvent } from "react";
import { ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { HoverTip } from "@/components/ui/tooltip";
import { fmtUsd } from "@/components/settings/providers/status/model";
import { formatTokens } from "@/lib/chat/context-occupancy";
import type { RosterStatus } from "@/lib/execution/agent-roster";
import { subAgentShape } from "@/lib/runtime-bridge/dag/render/agent-look";
import { AgentGlyph, type Text } from "./running-rows";
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

/** Left on a row folds the group it sits in; the agent's own row takes the
 *  focus, so Right opens it again from where the reader is. */
function foldOnLeft(onFold: (() => void) | undefined) {
  return (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "ArrowLeft" || !onFold || event.altKey || event.metaKey || event.ctrlKey) return;
    event.preventDefault();
    onFold();
  };
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
  onFold?: () => void;
}

export function RosterRow({ name, status, indent, cost = null, tip = "", outgoing = false, onOpen, onFold, text }: RosterRowProps & { text: Text }) {
  const row = <Button variant="ghost" className={styles.agentRow} style={{ ["--indent" as string]: `${indent}px` }}
    data-outgoing={outgoing ? "1" : undefined} data-roster-row="" onClick={onOpen} onKeyDown={foldOnLeft(onFold)}
    aria-label={outgoing ? `${text(`Message sent to ${name}`, `发给 ${name} 的消息`)}, ${rosterBadgeWords(status, text)}` : undefined}>
    <AgentGlyph shape={subAgentShape(name)} />
    {/* One line that gives up the cost first: when name, badge and cost do
        not fit, the cost wraps out of sight and the name keeps the room. */}
    <span className="flex h-[20px] min-w-0 flex-[1_1_0] flex-wrap items-center gap-x-[8px] overflow-hidden">
      <span className="flex h-[20px] max-w-full min-w-0 flex-auto items-center gap-[8px]">
        <span className={`${styles.name} min-w-0 flex-initial`}>{outgoing ? <span className={styles.arrow} aria-hidden="true">→ </span> : null}{name}</span>
        <RosterBadge status={status} text={text} />
      </span>
      {cost !== null ? <span className={styles.cost}>{fmtUsd(cost)}</span> : null}
    </span>
  </Button>;
  // The cost is on the tip too, since a narrow panel drops it from the row.
  const label = [cost !== null ? fmtUsd(cost) : "", tip].filter(Boolean).join(" · ");
  return label ? <HoverTip label={label} side="left">{row}</HoverTip> : row;
}

/** The quiet last row: how many helpers finished. It opens in place. */
export function FinishedRow({ count, open, indent, onToggle, onFold, text }: {
  count: number; open: boolean; indent: number; onToggle: () => void; onFold?: () => void; text: Text;
}) {
  return <Button variant="ghost" className={styles.agentRow} style={{ ["--indent" as string]: `${indent}px` }}
    data-finished="" data-roster-row="" aria-expanded={open} onClick={onToggle} onKeyDown={foldOnLeft(onFold)}>
    <span className={styles.finishedChevron} aria-hidden="true"><ChevronRight size={14} style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform var(--motion-medium) var(--ease-standard)" }} /></span>
    <span className={styles.finishedLabel}>{text(`${count} finished`, `${count} 个已完成`)}</span>
  </Button>;
}
