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

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Maximize2, Minus, Plus, Shapes } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { enterExclusiveCoverageMode } from "@/lib/runtime-bridge/dag";
import { fitCanvas, resetZoom, zoomStep } from "@/lib/runtime-bridge/dag/interaction/canvas";
import { MENU_PANEL } from "@/components/chat/top-bar/menu-styles";

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

/** Key for shape and coverage. Sits in the canvas HUD beside the fit
 *  button and the zoom readout; the body pops upward. Starts collapsed:
 *  the vocabulary is small and learnable. */
function DagLegend() {
  const { text } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  // 标准弹层收尾：点面板外任意处或按 Esc 关闭。pointerdown 在 click
  // 之前，命中图例自身（含按钮）时跳过，按钮自己的 onClick 负责开关。
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div className="dag-legend" ref={rootRef}>
      <button
        type="button"
        className="dag-hud-chip dag-legend-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <Shapes size={14} strokeWidth={2} />
        <span>{text("Legend", "图例")}</span>
      </button>
      {open && (
        // 面板框架（圆角/描边/底色/阴影/内衬）来自 MENU_PANEL——全应用
        // 弹层菜单共用的那一份视觉定义；.dag-legend-body 只负责锚位
        // （向上弹）和行的排版。
        <div className={`dag-legend-body ${MENU_PANEL}`}>
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

/** Canvas controls at the composer's TOP-RIGHT — the right end of its
 *  env-chip row: fit the graph back into view, step / reset the zoom
 *  (− · readout · +), open the legend. Chip-sized like the env chips
 *  beside them — and dressed by the SAME env-pill rule
 *  (composer.module.css), not a private lookalike. Portaled
 *  into the composer's `#dagHudSlot` so they ride the composer wherever
 *  it sits and however tall it grows. Rendered only while the DAG
 *  perspective is showing — the slot stays an empty div in chat. The
 *  zoom readout is written by `dag/canvas.ts` on every view change — it
 *  is the camera's number, and routing it through React state would
 *  repaint the tree on every wheel event of a gesture. */
function DagHud({ active }: { active: boolean }) {
  const { text } = useTranslation();
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  // The slot lives in the composer, which may mount after this view (and
  // may be replaced on a session switch) — poll a few frames until it is
  // there, and re-find whenever the one we hold left the document.
  useEffect(() => {
    if (!active) return;
    if (slot && slot.isConnected) return;
    let raf = 0;
    let tries = 0;
    const find = (): void => {
      const el = document.getElementById("dagHudSlot");
      if (el) {
        setSlot(el);
      } else if (tries++ < 120) {
        raf = requestAnimationFrame(find);
      }
    };
    find();
    return () => cancelAnimationFrame(raf);
  }, [active, slot]);
  if (!active || !slot || !slot.isConnected) return null;
  return createPortal(
    <div className="dag-hud">
      <button
        type="button"
        className="dag-hud-chip"
        onClick={() => fitCanvas()}
        title={text("Fit graph to view", "缩放到全图")}
      >
        <Maximize2 size={14} strokeWidth={2} />
        <span>{text("Fit", "全图")}</span>
      </button>
      {/* 缩放簇：一颗胶囊里 [−] [倍率] [+]。−/+ 步进一个滚轮格，
          倍率数字本身点击重置 100%——都以画布中心为锚
          （canvas.ts::zoomStep / resetZoom）。 */}
      <div className="dag-hud-chip dag-hud-zoomctl">
        <button
          type="button"
          className="dag-hud-zoombtn"
          onClick={() => zoomStep(-1)}
          title={text("Zoom out", "缩小")}
          aria-label={text("Zoom out", "缩小")}
        >
          <Minus size={14} strokeWidth={2} />
        </button>
        <button
          type="button"
          className="dag-hud-zoom"
          onClick={() => resetZoom()}
          title={text("Reset zoom to 100%", "重置为 100%")}
        >
          100%
        </button>
        <button
          type="button"
          className="dag-hud-zoombtn"
          onClick={() => zoomStep(1)}
          title={text("Zoom in", "放大")}
          aria-label={text("Zoom in", "放大")}
        >
          <Plus size={14} strokeWidth={2} />
        </button>
      </div>
      <DagLegend />
    </div>,
    slot,
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
      <DagHud active={visible} />
    </div>
  );
}
