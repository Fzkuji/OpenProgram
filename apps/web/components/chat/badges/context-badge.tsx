"use client";

/**
 * Per-conversation token-usage badge for the Composer's bottom row.
 *
 * Replaces the legacy `#tokenBadge` DOM node (defined in
 * `public/html/index.html`) and the imperative render code in
 * `public/js/shared/providers.js` (`_renderTokenBadge` /
 * `refreshTokenBadge`). Both legacy paths now push the latest
 * `{input, output, cache_read}` tuple into the Zustand store via
 * `setContextStats`; this component is the sole renderer.
 *
 * Visual: a small progress ring (Claude Code style — 12px svg in a
 * 20px button, var(--border) track, var(--accent-orange) arc starting
 * at 12 o'clock). ALWAYS renders: a session with no usage yet (or no
 * session at all) shows the empty ring at 0 progress instead of
 * vanishing from the controls row. Tooltip carries the
 * "Context used / window (pct)" breakdown.
 */
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { useSessionStore } from "@/lib/session-store";
import { useSessionScope } from "@/lib/session-store/session-scope";
import { HoverTip } from "@/components/ui/tooltip";
import { FillIcon } from "@/components/ui/fill-icon";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { translateText } from "@/lib/i18n";
import { ContextBreakdownPanel } from "./context-breakdown-panel";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  hasContextBreakdown,
  readContextBreakdownCache,
  readDraftContextBreakdown,
  subscribeContextBreakdownCache,
  warmContextBreakdown,
  warmDraftContextBreakdown,
  writeContextBreakdownCache,
} from "@/lib/chat/context-breakdown-cache";
import { isCompacting } from "@/lib/chat/run/run-operation";
import { contextTip, occupancyOf, occupancySnapshot, statsSnapshot } from "@/lib/chat/context-occupancy";

interface ContextBadgeProps {
  /** Active conversation id. The component is keyed on this so a
   *  session switch immediately drops to "no data" until the new
   *  session's usage arrives. Accepts ``string | null`` (no session) and
   *  the legacy ``sessionId`` prop name preserved for the unmigrated
   *  `<ChatView>` call site. */
  sessionId?: string | null;
}

/** Whether `ref`'s element is laid out (not inside a display:none shell).
 *  A ResizeObserver reports the change both ways: an element that stops
 *  being rendered shrinks to 0×0, one that comes back grows again. */
function useLaidOut(ref: RefObject<HTMLElement | null>): boolean {
  const [laidOut, setLaidOut] = useState(false);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    const update = () => setLaidOut(node.getClientRects().length > 0);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref]);
  return laidOut;
}

export function ContextBadge({ sessionId }: ContextBadgeProps) {
  // Resolve session: caller may pass `sessionId` (legacy ChatView path)
  // or omit it (Composer path) — in the latter case we fall back to the
  // store's current conversation id.
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const sid = sessionId ?? currentSessionId;

  // 点 badge 弹出 /context 分类分解面板（随时看当前会话 context 构成）。
  // open 状态放本会话的 scope store —— /context slash 命令也能切它，而分屏
  // 时两个 badge 各读各的，点一个不会两边同时弹。
  const scopeOpen = useSessionScope((s) => s.contextPanelOpen);
  const setPanelOpen = useSessionScope((s) => s.setContextPanelOpen);
  // The same conversation can have a second, hidden composer (the
  // single-pane shell stays mounted under display:none in a split view)
  // reading the same scope; only a ring that is laid out opens its card,
  // or the hidden one's card would portal out to the window's corner.
  const ringRef = useRef<HTMLButtonElement>(null);
  const panelOpen = useLaidOut(ringRef) && scopeOpen;
  // 当前分支头：切分支时 store.heads 会更新，弹窗据此重取该分支的上下文。
  const headId = useSessionStore((s) => (sid ? s.heads[sid] : undefined));

  const sessionUsage = useSessionStore((s) => (sid ? s.tokens[sid] : undefined));
  const sessionWindow = useSessionStore((s) => (sid ? s.contextWindow[sid] : undefined));
  const compacting = useSessionStore((s) =>
    Boolean(sid && isCompacting(s.runningTasks[sid])),
  );

  // Paint from the ring the moment this session is focused. GET /context
  // only fills category detail, and never blocks the click.
  useEffect(() => {
    if (!sid) return;
    const fromStats = statsSnapshot(sessionUsage, sessionWindow);
    if (!readContextBreakdownCache(sid, headId ?? null) && fromStats) {
      writeContextBreakdownCache(sid, headId ?? null, fromStats);
    }
    // Another branch's snapshot standing in (the session's latest) is
    // counted against that branch's model: this branch reads its own.
    if (!hasContextBreakdown(sid, headId ?? null)
        || readContextBreakdownCache(sid, headId ?? null)?.tools == null) {
      warmContextBreakdown(sid, headId ?? null, { focus: true });
    }
  }, [sid, headId, sessionUsage, sessionWindow]);

  // The shared Popover owns placement (above the ring, right edges aligned,
  // lifted 9px: the row's 10px band gap minus the 1px ring, so it sits on
  // the input box like the other bottom-row popovers), Escape and a press
  // or focus outside; a press on the ring itself is never "outside". A
  // press on a native page (a web tab, the floating preview) never reaches
  // this document, it only takes the window's focus away, so blur closes
  // too.
  useEffect(() => {
    if (!panelOpen) return;
    const onBlur = () => setPanelOpen(false);
    window.addEventListener("blur", onBlur);
    return () => window.removeEventListener("blur", onBlur);
  }, [panelOpen, setPanelOpen]);
  // A click on the ring toggles from the state the press found. Anything
  // that closes the card during the press (the window blurring as focus
  // comes back from a native page) must not let the click read "closed"
  // and open it again: that was the close-then-reopen flash. A keyboard
  // click has no press and toggles the current state.
  const openAtPress = useRef<boolean | null>(null);

  const fallbackProvider = useSessionStore((s) => s.agentSettings.chat?.provider);
  const fallbackModel = useSessionStore((s) => s.agentSettings.chat?.model);
  const draftProjectId = useSessionStore((s) =>
    s.activeChatKey ? s.pendingProjectsByChat[s.activeChatKey] ?? null : null);
  // The ring, its tip and the /context card read one snapshot
  // (lib/chat/context-occupancy), so they always show the same number: the
  // session's last context_stats, and before the first one the card's
  // cached snapshot (a new chat's estimate); with no session yet, the
  // draft estimate of what the first message would carry. When the first
  // message's ack makes the draft a session, chat-stream copies that
  // estimate into the session's snapshot (adoptDraftContextBreakdown), so
  // the number and window hold until GET /context and context_stats
  // replace them.
  const readSnapshot = () => sid
    ? readContextBreakdownCache(sid, headId ?? null)
    : readDraftContextBreakdown(fallbackProvider, fallbackModel, draftProjectId);
  const [cached, setCached] = useState(readSnapshot);
  useEffect(() => {
    const sync = () => setCached(readSnapshot());
    sync();
    if (!sid) warmDraftContextBreakdown(fallbackProvider, fallbackModel, draftProjectId);
    return subscribeContextBreakdownCache(sync);
    // readSnapshot reads only these.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid, headId, fallbackProvider, fallbackModel, draftProjectId]);
  const snapshot = sid ? occupancySnapshot(cached, sessionUsage, sessionWindow) : cached;
  const occupancy = occupancyOf(snapshot);

  // 永远渲染圆环：还没有 usage 的会话（或还没有会话）显示 0 进度的空环，
  // 而不是整个消失（“圆环不见了”就是以前 return null 造成的）。
  // tooltip: 用量 + 模型/provider 元信息。context_stats 里的 model 比
  // agentSettings 更精确（单 turn 内可能切了 provider，agentSettings 是最终值）。
  const modelLabel = sessionUsage?.model || snapshot?.model || fallbackModel || "";
  const providerLabel = sessionUsage?.provider || fallbackProvider || "";
  const metaLine = [providerLabel, modelLabel].filter(Boolean).join(" · ");

  // 窗口未知（模型解析不到）时为 0：不编一个上限，圆环不填充，提示只写用量。
  const win = occupancy.window;
  const pct = win > 0 ? Math.max(0, Math.min(1, occupancy.used / win)) : 0;

  // 占用越高颜色越警示：<50% 绿，50–80% 黄，≥80% 红。
  const arcColor = pct >= 0.8 ? "var(--danger-soft)" : pct >= 0.5 ? "var(--warning-soft)" : "var(--success-soft)";

  // Claude Code 那种「Context 用了多少/共多少 (百分比)」；估算值标 est.
  // （measured = 上一次真实请求实测，estimated = 之后图变了按当前图重估）。
  const ringTooltip = compacting
    ? translateText("Compacting context…", "正在压缩上下文…")
    : contextTip(occupancy, metaLine);

  // 环形进度（Claude Code 实测：12px svg、描边 2、轨道 var(--border)、
  // 进度 var(--accent-orange)、-90° 起点）
  const R = 5;               // 半径 — 12px viewBox 里留 1px 描边余量
  const SW = 2;              // 描边宽度
  const C = 2 * Math.PI * R; // 周长

  return (
    <Popover open={panelOpen} onOpenChange={setPanelOpen}>
      {/* HoverTip = 全站统一黑提示（原生 title 又慢又不是黑卡）；
          aria-expanded（PopoverTrigger 标的）让面板开着时不冒提示。 */}
      <HoverTip label={ringTooltip}>
      <PopoverTrigger asChild>
      <button
        ref={ringRef}
        type="button"
        className={
          cn(buttonVariants({ variant: "elevated", size: "icon-sm" }))
          + " context-ring-badge"
          + (compacting ? " is-busy" : "")
        }
        onPointerDown={() => { openAtPress.current = panelOpen; }}
        onClick={(event) => {
          const wasOpen = openAtPress.current ?? panelOpen;
          openAtPress.current = null;
          // Skips the trigger's own toggle, which reads the current state.
          event.preventDefault();
          setPanelOpen(!wasOpen);
        }}
        aria-label="Context usage"
      >
        {/* The ring already fills its 16 px box (CSS sizes the svg); the
            box grows to 18 px on hover like every composer icon. */}
        <FillIcon crop={false}>
        <svg width="12" height="12" viewBox="0 0 12 12">
          <circle
            cx="6"
            cy="6"
            r={R}
            fill="none"
            stroke="var(--border)"
            strokeWidth={SW}
          />
          <circle
            cx="6"
            cy="6"
            r={R}
            fill="none"
            stroke={arcColor}
            strokeWidth={SW}
            strokeLinecap="round"
            style={{ transition: "stroke 400ms ease, stroke-dashoffset 400ms ease" }}
            strokeDasharray={C}
            strokeDashoffset={C * (1 - pct)}
            transform="rotate(-90 6 6)"
          />
        </svg>
        </FillIcon>
      </button>
      </PopoverTrigger>
      </HoverTip>
      {/* The card alone takes down a native page (a web tab, the floating
          preview) it overlaps; one elsewhere stays live. Focus stays where
          it was (the composer when /context opened it). */}
      <PopoverContent
        data-native-view-occluder="true"
        side="top"
        align="end"
        sideOffset={9}
        className="w-auto border-0 bg-transparent p-0 shadow-none"
        onOpenAutoFocus={(event) => event.preventDefault()}
        onCloseAutoFocus={(event) => event.preventDefault()}
      >
        <ContextBreakdownPanel
          key={JSON.stringify([sid, headId ?? null])}
          sessionId={sid}
          headId={headId ?? null}
          onClose={() => setPanelOpen(false)}
        />
      </PopoverContent>
    </Popover>
  );
}
