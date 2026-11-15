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
 * each lane's tail name tag (render/labels/badges.ts) is the checkout button,
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

import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { SolarIcon } from "@/components/solar-icons";
import type { SolarIconName } from "@/components/solar-icons/bodies";

import { useTranslation } from "@/lib/i18n";
import { getSocket, runtimeState } from "@/lib/runtime-bridge/state";
import { useSessionStore } from "@/lib/session-store";
import { enterExclusiveCoverageMode, renderHistoryGraph, setDagAgentLook } from "@/lib/runtime-bridge/dag";
import { useConversationAgentShape } from "@/components/agents/agent-avatar";
import { BotGlyph } from "@/components/avatar/bot/bot-glyph";
import { LANE_COLORS } from "@/lib/format-utils/lane-colors";
import { subAgentShape } from "@/lib/runtime-bridge/dag/render/agent-look";
import { focusAgent, resetZoom, zoomStep } from "@/lib/runtime-bridge/dag/interaction/view/canvas";
import { minimapOn, stowMinimap, subscribeMinimap, toggleMinimap } from "@/lib/runtime-bridge/dag/interaction/view/minimap";
import { MENU_PANEL } from "@/components/ui/menu/menu-styles";
import { DagCanvasTools } from "@/components/chat/dag/dag-tools";
import { DagKeySheet, PANEL_MOTION, useKeySheetOpen } from "@/components/chat/dag/dag-key-sheet";
import { PipDockSlot } from "@/components/center-tabs/pip/pip-dock-slot";
import { keySheetOpen, setKeySheet } from "@/lib/runtime-bridge/dag/interaction/selection/key-sheet";

/** A tile swatch: the same `dag-tile t-<tone>` classes the canvas tiles
 *  carry (render/tiles.ts, app/styles/dag/graph/nodes.css), so the key and the
 *  canvas can never drift apart. */
function LegendTile({ icon, bot, tone, state = "covered" }: {
  icon?: SolarIconName;
  /** An agent's character instead of an icon, in the trunk's colour, as the
   *  canvas draws it on the main branch (render/nodes.ts). */
  bot?: string;
  tone: string;
  state?: "covered" | "outline" | "error";
}) {
  return (
    <span className={`dag-tile t-${tone} dag-legend-tile`} data-state={state} aria-hidden="true"
      style={bot ? ({ "--tone": LANE_COLORS[0] } as React.CSSProperties) : undefined}>
      {bot ? <BotGlyph shape={bot} size={16} /> : icon ? <SolarIcon name={icon} size={16} motionPreset="none" /> : null}
    </span>
  );
}

function LegendRow({ swatch, label }: { swatch: React.ReactNode; label: string }) {
  return (
    <div className="dag-legend-row">
      {swatch}
      <span>{label}</span>
    </div>
  );
}

/** Key for shape and coverage. A switch in the zoom column, like the
 *  minimap's: it stays open until its button is pressed again — a
 *  click on the canvas or on the map button leaves it open. It and the
 *  controls sheet can be open together: the side column scrolls.
 *  Starts closed: the vocabulary is small and learnable. */
function DagLegend() {
  const { text } = useTranslation();
  const agentShape = useConversationAgentShape(useSessionStore((s) => s.currentSessionId));
  const [open, setOpen] = useState(false);
  // The panel renders into the side column, under the map and the docked
  // web preview, above the controls sheet (dag/panels/tools.css
  // .dag-side-column), whatever the strips above the zoom column hold.
  const [host, setHost] = useState<HTMLElement | null>(null);
  return (
    <div className="dag-legend" ref={(el) => setHost(el?.closest<HTMLElement>(".dag-view")?.querySelector<HTMLElement>(":scope > .dag-side-column") ?? null)}>
      <button
        type="button"
        className="dag-tool-btn"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-pressed={open}
        aria-label={text("Legend", "图例")}
        data-tip={open ? undefined : text("Legend", "图例")}
        data-tip-side="left"
      >
        <SolarIcon name="widget-2" size={16} />
      </button>
      {host && createPortal(
      <AnimatePresence>
      {open && (
        // 外框同操作说明（dag/panels/tools.css .dag-side-panel），整列一起滚，
        // 面板自己不滚；.dag-legend-body 只管行的排版；
        // 出现和收起的动效同 PANEL_MOTION（app 弹层的那一套）。
        <motion.div key="legend" {...PANEL_MOTION} className={`dag-side-panel dag-legend-body ${MENU_PANEL}`}>
          <LegendRow swatch={<LegendTile icon="user-rounded" tone="user" />} label={text("your message", "你的消息")} />
          <LegendRow swatch={<LegendTile bot={agentShape} tone="llm" />} label={text("agent reply", "agent 回复")} />
          <LegendRow swatch={<LegendTile icon="programming" tone="shell" />}
            label={text("tool call — same icon as in the chat", "工具调用，图标与对话里一致")} />
          <LegendRow swatch={<LegendTile bot={subAgentShape("")} tone="agent" />} label={text("a sub-agent starts", "子 agent 起点")} />
          <LegendRow swatch={<LegendTile icon="archive" tone="memory" />} label={text("compaction summary", "压缩摘要")} />
          <LegendRow swatch={<LegendTile bot={agentShape} tone="llm" state="outline" />}
            label={text("faint: not in the next request", "浅色：不在下次请求里")} />
          <LegendRow swatch={<LegendTile icon="programming" tone="shell" state="error" />}
            label={text("failed", "失败")} />
          <LegendRow swatch={<span className="dag-legend-agent" aria-hidden="true"><LegendTile bot={agentShape} tone="llm" /></span>}
            label={text("an agent, on the step it is working on", "agent 站在它正在做的那一步上")} />
          <LegendRow swatch={<span className="dag-legend-belt" aria-hidden="true" />}
            label={text("goods on a belt: work is under way there", "传送带运货：那里正在工作")} />
        </motion.div>
      )}
      </AnimatePresence>,
      host)}
    </div>
  );
}

/** The zoom column in the canvas's top-right corner, right under the
 *  pane's perspective strip (dag/rendering.md §13): one raised strip
 *  holding zoom in, the readout (click: back to 100%), zoom out and,
 *  under a hairline, find the working agent (full screen is the strip
 *  above's, shared by both perspectives);
 *  a second strip under it turns the legend and the minimap on and off
 *  and, under a hairline, opens the controls sheet (dag-key-sheet.tsx).
 *  All open to the column's left from the top, the map above the
 *  legend. Every glyph is Solar Bold Duotone with its hover pop, like
 *  the composer's buttons. The readout is written by
 *  `dag/interaction/view/canvas.ts` on every view change — it is the
 *  camera's number, and routing it through React state would repaint
 *  the tree on every wheel event of a gesture. */
function DagZoomColumn() {
  const { text } = useTranslation();
  const mapOn = useSyncExternalStore(subscribeMinimap, minimapOn, minimapOn);
  const sheetOpen = useKeySheetOpen();
  return (
    <div className="dag-zoom-column">
      <div className="dag-tool-rail" role="toolbar" aria-orientation="vertical"
        aria-label={text("Zoom", "缩放")}>
        <button type="button" className="dag-tool-btn" onClick={() => zoomStep(1)}
          aria-label={text("Zoom in", "放大")} aria-keyshortcuts="+"
          data-tip={text("Zoom in", "放大")} data-tip-keys="+" data-tip-side="left">
          <SolarIcon name="magnifer-zoom-in" size={16} />
        </button>
        <button type="button" className="dag-tool-btn dag-zoom-readout" onClick={() => resetZoom()}
          aria-keyshortcuts="Shift+0"
          data-tip={text("Back to 100%", "回到 100%")} data-tip-keys="⇧ 0" data-tip-side="left">
          100%
        </button>
        <button type="button" className="dag-tool-btn" onClick={() => zoomStep(-1)}
          aria-label={text("Zoom out", "缩小")} aria-keyshortcuts="-"
          data-tip={text("Zoom out", "缩小")} data-tip-keys="−" data-tip-side="left">
          <SolarIcon name="magnifer-zoom-out" size={16} />
        </button>
        <span className="dag-tool-sep" aria-hidden="true" />
        <button type="button" className="dag-tool-btn" onClick={() => focusAgent()}
          aria-label={text("Find the working agent", "找到正在工作的 agent")} aria-keyshortcuts="F"
          data-tip={text("Find the agent\nAgain for the next one", "找到 agent\n再按换下一个")}
          data-tip-keys="F" data-tip-side="left">
          <SolarIcon name="gps" size={16} />
        </button>
      </div>
      <div className="dag-tool-rail" role="toolbar" aria-orientation="vertical"
        aria-label={text("Minimap and guides", "小地图和指南")}>
        <button type="button" className="dag-tool-btn" onClick={() => toggleMinimap()}
          aria-pressed={mapOn} aria-label={text("Minimap", "小地图")} aria-keyshortcuts="M"
          data-tip={mapOn ? text("Hide the minimap", "隐藏小地图") : text("Show the minimap", "显示小地图")}
          data-tip-keys="M"
          data-tip-side="left">
          <SolarIcon name="map" size={16} />
        </button>
        <span className="dag-tool-sep" aria-hidden="true" />
        {/* The two guides — what the marks mean, what the canvas answers
            to — share a group, and only one of them is open at a time. */}
        <DagLegend />
        <button type="button" className="dag-tool-btn" data-key-sheet-toggle=""
          onClick={() => setKeySheet(!keySheetOpen())}
          aria-pressed={sheetOpen} aria-label={text("Controls", "操作说明")} aria-keyshortcuts="?"
          data-tip={sheetOpen ? undefined : text("Controls", "操作说明")} data-tip-keys="?"
          data-tip-side="left">
          <SolarIcon name="notes" size={16} />
        </button>
      </div>
    </div>
  );
}

export function DagView({ visible }: { visible: boolean }) {
  const sessionId=useSessionStore(s=>s.currentSessionId);
  // Replies on the graph wear the conversation Agent's character.
  const agentShape=useConversationAgentShape(sessionId);
  useEffect(()=>{ setDagAgentLook(agentShape); },[agentShape]);
  const rootRef=useRef<HTMLDivElement|null>(null);
  // Before the effect below asks for the graph: the map comes in once,
  // when the graph does (minimap.ts stowMinimap).
  useLayoutEffect(()=>{ if(visible && rootRef.current) stowMinimap(rootRef.current); },[visible,sessionId]);
  // The composer floats over the canvas's foot, so the side panels stop
  // 16px above it rather than at the canvas's edge, where their last rows
  // would sit under the input box with nothing left to scroll.
  useLayoutEffect(()=>{
    const root=rootRef.current;
    if(!visible || !root) return;
    // `#composer-mount` is a static anchor; the box is its absolutely
    // placed child, which page-shell.tsx portals in after mount — and
    // which grows with a decision card or the run status above the input.
    let box:Element|null=null;
    let queued=false;
    let live=true;
    const measure=()=>{
      queued=false;
      if(!live) return;
      const next=document.getElementById("composer-mount")?.firstElementChild ?? null;
      if(next!==box){ if(box) ro.unobserve(box); box=next; if(box) ro.observe(box); }
      if(!box){ root.style.removeProperty("--dag-bottom-inset"); return; }
      const inset=root.getBoundingClientRect().bottom-box.getBoundingClientRect().top;
      root.style.setProperty("--dag-bottom-inset",`${Math.max(16,Math.ceil(inset)+16)}px`);
    };
    const schedule=()=>{ if(!queued){ queued=true; queueMicrotask(measure); } };
    const ro=new ResizeObserver(schedule);
    ro.observe(root);
    // Watched from the pane, which exists before either of its children:
    // a graph restored at startup mounts before `#chatView` and the
    // composer do, and must still find the box when they arrive.
    const mo=new MutationObserver(schedule);
    if(root.parentElement) mo.observe(root.parentElement,{childList:true,subtree:true});
    measure();
    return ()=>{ live=false; ro.disconnect(); mo.disconnect(); };
  },[visible]);
  useEffect(()=>{
    const send=()=>{
      const sock=getSocket();
      if(!sessionId || !sock || sock.readyState!==WebSocket.OPEN)return;
      sock.send(JSON.stringify({action:"list_branches",session_id:sessionId,graph_visible:visible,include_graph:visible}));
      if(!visible){
        renderHistoryGraph([],null);
        const conv=runtimeState.conversations[sessionId];
        if(conv)conv.graph=[];
      }
    };
    let stopped=false;
    const connected=()=>queueMicrotask(()=>{if(!stopped)send();});
    window.addEventListener("op:browser-connection",connected);
    send();
    return ()=>{
      stopped=true;window.removeEventListener("op:browser-connection",connected);
      const sock=getSocket();
      if(visible && sessionId && sock?.readyState===WebSocket.OPEN){
        sock.send(JSON.stringify({action:"list_branches",session_id:sessionId,graph_visible:false}));
        renderHistoryGraph([],null);
        const conv=runtimeState.conversations[sessionId];if(conv)conv.graph=[];
      }
    };
  },[visible,sessionId]);
  useEffect(() => {
    if (visible) enterExclusiveCoverageMode();
  }, [visible]);
  return (
    <div
      ref={rootRef}
      id="historyPanel"
      className="dag-view"
      style={{ display: visible ? "flex" : "none" }}
      aria-hidden={visible ? undefined : true}
    >
      {/* 分支切换直接在图内：每条 lane 尾部的分支名标签就是按钮
          （render/labels/badges.ts），点非活动分支即 checkout。页面顶部不再
          放分支条——那条横线会横穿右上角的悬浮视角按钮。 */}
      <div className="history-body"></div>
      {visible && <DagCanvasTools />}
      {/* The minimap (minimap.ts puts its canvas in the slot), the docked
          web preview, then the guides, in one column that scrolls as a
          whole, like the macOS widgets sidebar (dag/panels/tools.css). */}
      {visible && (
        <div className="dag-side-column">
          <div className="dag-minimap-slot" />
          <PipDockSlot sessionId={sessionId} />
          <DagKeySheet />
        </div>
      )}
      {visible && <DagZoomColumn />}
    </div>
  );
}
