"use client";

/**
 * DagView — the session context DAG as a CENTER perspective.
 *
 * The graph used to be a right-sidebar tab, squeezed into a 288px rail.
 * It now takes the whole center column: same host DOM (`#historyPanel`
 * + `.history-body`), which is what `lib/runtime-bridge/dag` selects
 * imperatively, so the renderer needed no change — only a wider box.
 * `_wirePanelResize` (render/visibility.ts) already re-lays-out on host
 * width changes, so the switch and any window resize re-flow the graph.
 *
 * Mounted alongside the transcript and toggled with `display`, not
 * unmounted: the renderer writes into this DOM on every WebSocket
 * capture regardless of which perspective is showing, so tearing the
 * host down would drop the graph until the next capture.
 *
 * Layout: the canvas, and — supplied by the pane, not by this
 * component — the composer. Branch switching lives INSIDE the graph:
 * each lane's tail name tag (render/badges.ts) is the checkout button,
 * so no strip row sits above the canvas. The composer is a
 * singleton anchored inside `#chatView`, which the graph perspective
 * deliberately leaves mounted (it hides `#chatArea` instead), so
 * sending a message from the graph runs the same code path as sending
 * it from the transcript and the new node appears on the next capture.
 *
 * The white fill means context coverage here, with no mode switch to
 * offer (dag/rendering.md §8). Viewport highlighting answers "which
 * bubbles are on screen", and whenever this component is visible it
 * owns its pane with no transcript beside it: a lone session tab gets
 * the chat shell and this graph, and two session tabs split into two
 * `PeerSessionPane`s where the shell — and therefore this graph — is
 * not rendered at all. So the question has no reading to give, and
 * coverage is simply what the fill means.
 */

import { useEffect, useState } from "react";

import { useTranslation } from "@/lib/i18n";
import { enterExclusiveCoverageMode } from "@/lib/runtime-bridge/dag";

const STROKE = "var(--accent-primary, #4a7dfc)";
const GHOST = "var(--dag-ghost, #c9c7bf)";

/** Shape swatches, drawn with the same primitives `shapes.ts` uses so
 *  the key and the canvas can never drift apart. */
const SHAPES: Record<string, React.ReactNode> = {
  root: <rect x="3" y="3" width="9" height="9" transform="rotate(45 7.5 7.5)"
    fill="none" stroke={STROKE} strokeWidth="1.6" />,
  user: <circle cx="7.5" cy="7.5" r="5" fill="none" stroke={STROKE} strokeWidth="1.6" />,
  llm: <polygon points="7.5,2 12.5,12 2.5,12" fill="none" stroke={STROKE} strokeWidth="1.6" />,
  tool: <rect x="2.5" y="2.5" width="10" height="10" fill="none" stroke={STROKE} strokeWidth="1.6" />,
  ghost: <circle cx="7.5" cy="7.5" r="5" fill="none" stroke={GHOST} strokeWidth="1.4" />,
  covered: (
    <>
      <circle cx="7.5" cy="7.5" r="5" fill="none" stroke={STROKE} strokeWidth="1.6" />
      <circle cx="7.5" cy="7.5" r="2" fill="#fff" stroke="#9a9890" strokeWidth=".8" />
    </>
  ),
};

function LegendRow({ shape, label }: { shape: React.ReactNode; label: string }) {
  return (
    <div className="dag-legend-row">
      <svg width="15" height="15" aria-hidden="true">{shape}</svg>
      <span>{label}</span>
    </div>
  );
}

/** Key for shape and coverage. Lives in the composer's env-chip row
 *  (right end), sized like its neighbour chips; the body pops upward.
 *  Starts collapsed: the vocabulary is small and learnable. */
export function DagLegend() {
  const { text } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <div className="dag-legend">
      <button
        type="button"
        className="dag-legend-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {open ? "▾ " : "▸ "}{text("Legend", "图例")}
      </button>
      {open && (
        <div className="dag-legend-body">
          <LegendRow shape={SHAPES.root} label={text("root", "root")} />
          <LegendRow shape={SHAPES.user} label={text("user turn", "用户轮")} />
          <LegendRow shape={SHAPES.llm} label={text("model reply", "模型回复")} />
          <LegendRow shape={SHAPES.tool} label={text("code / tool", "代码/工具")} />
          <LegendRow
            shape={
              <rect x="1" y="4" width="13" height="7" rx="3.5"
                fill="none" stroke={STROKE} strokeWidth="1.6" />
            }
            label={text("compaction summary", "压缩摘要")}
          />
          <LegendRow
            shape={SHAPES.covered}
            label={text("in the next request", "在下次请求里")}
          />
          <LegendRow
            shape={SHAPES.ghost}
            label={text("archived — folded or failed", "已留档 · 折叠或失败")}
          />
        </div>
      )}
    </div>
  );
}

export function DagView({ visible }: { visible: boolean }) {
  useEffect(() => {
    if (visible) enterExclusiveCoverageMode();
  }, [visible]);
  return (
    <div
      id="historyPanel"
      className="dag-view"
      style={{ display: visible ? "flex" : "none" }}
      aria-hidden={visible ? undefined : true}
    >
      {/* 分支切换直接在图内：每条 lane 尾部的分支名标签就是按钮
          （render/badges.ts），点非活动分支即 checkout。页面顶部不再
          放分支条——那条横线会横穿右上角的悬浮视角按钮。 */}
      <div className="history-body"></div>
    </div>
  );
}
