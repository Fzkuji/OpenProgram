"use client";

/**
 * Overview pane of Settings → LLM Providers: every session (and its
 * branches, sub-agents and child sessions) that is talking to a model right
 * now or did so inside the chosen window, wired to the models it uses.
 *
 * Data comes from GET /api/routing/overview (routes/settings/routing.py).
 * The map is two HTML columns with an SVG layer behind them; edge end
 * points are measured from the rendered rows, so text wraps and the
 * container resizes freely. Live lanes carry a dot moving along the edge;
 * a lane whose request was switched away from a failing model keeps a
 * dashed edge to that model. Too narrow for two columns and the edges
 * between them, the pane says so and asks for room instead of a cramped map.
 */
import { motion } from "framer-motion";
import { Bot, Code2, GitBranch, Layers, PanelLeftClose, Waypoints } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useTranslation } from "@/lib/i18n";
import { openSession } from "@/lib/tabs/navigation/open-session";

import { ProviderIcon } from "./icons/provider-icon";
import styles from "./routing-overview.module.css";
import type { Provider } from "./types";

type LaneKind = "branch" | "sub_agent" | "children" | "calls" | "background";

interface Lane {
  kind: LaneKind;
  id: string;
  name: string;
  trunk: boolean;
  provider: string;
  model: string;
  count?: number;
  events?: number;
  updated_at: number;
  active: boolean;
  live: boolean;
}

interface SessionRow {
  id: string;
  /** "background" groups calls no listed session accounts for. */
  kind?: "background";
  title: string;
  source: string;
  channel: string;
  running: boolean;
  updated_at: number;
  lanes: Lane[];
  hidden_branches: number;
}

interface RouteEvent {
  ts: number;
  session_id: string | null;
  parent_session_id: string | null;
  session_title: string;
  from_provider: string;
  from_model: string;
  to_provider: string;
  to_model: string;
  reason: string;
}

interface Account {
  label: string;
  status: string;
  until_ms: number;
}

interface Overview {
  now: number;
  window: number;
  sessions: SessionRow[];
  idle_sessions: number;
  accounts: Record<string, Account[]>;
  events: RouteEvent[];
  // Some sessions were still being opened on the server; ask again soon.
  partial?: boolean;
  stats: { requests: number; reroutes: number };
}

const WINDOWS = { live: 1800, day: 86400, week: 7 * 86400 } as const;
type WindowKey = keyof typeof WINDOWS;

const modelKey = (provider: string, model: string) => `${provider}\u0000${model}`;
const laneKey = (session: string, lane: string) => `${session}\u0000${lane}`;

interface Edge {
  id: string;
  lane: string;
  model: string;
  live: boolean;
  failed: boolean;
}

interface Point { x: number; y: number }

interface ModelEntry { provider: string; model: string; lanes: number; live: number }

export function RoutingOverview({
  providers,
  onSelectProvider,
}: {
  providers: Provider[];
  onSelectProvider: (id: string) => void;
}) {
  const { text } = useTranslation();
  const [windowKey, setWindowKey] = useState<WindowKey>("live");
  const [data, setData] = useState<Overview | null>(null);
  // The window the shown data belongs to; differs from windowKey while a
  // switch is loading, so the old numbers are dimmed instead of passing for
  // the new window.
  const [dataKey, setDataKey] = useState<WindowKey | null>(null);
  const [failed, setFailed] = useState(false);
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // A switch drops the previous window's request instead of letting it
    // finish behind the new one.
    const controller = new AbortController();
    const load = async () => {
      let partial = false;
      if (document.visibilityState === "visible") {
        try {
          const res = await fetch(`/api/routing/overview?window=${WINDOWS[windowKey]}`, { signal: controller.signal });
          if (!res.ok) throw new Error(String(res.status));
          const next = (await res.json()) as Overview;
          partial = !!next.partial;
          if (!stopped) { setData(next); setDataKey(windowKey); setFailed(false); }
        } catch {
          if (!stopped) setFailed(true);
        }
      }
      if (!stopped) timer = setTimeout(load, partial ? 2000 : windowKey === "live" ? 4000 : 15000);
    };
    void load();
    return () => { stopped = true; clearTimeout(timer); controller.abort(); };
  }, [windowKey]);

  const labels = useMemo(() => new Map(providers.map((p) => [p.id, p.label])), [providers]);

  // The model column: every model a visible lane uses, plus the models the
  // failover chain just switched away from, grouped by provider.
  const { groups, edges } = useMemo(() => {
    const models = new Map<string, ModelEntry>();
    const edgeList: Edge[] = [];
    const touch = (provider: string, model: string) => {
      const key = modelKey(provider, model);
      let entry = models.get(key);
      if (!entry) { entry = { provider, model, lanes: 0, live: 0 }; models.set(key, entry); }
      return entry;
    };
    const lastSwitch = new Map<string, RouteEvent>();
    for (const e of data?.events ?? []) {
      const sid = e.parent_session_id || e.session_id;
      if (sid && !lastSwitch.has(sid)) lastSwitch.set(sid, e);
    }
    for (const s of data?.sessions ?? []) {
      for (const lane of s.lanes) {
        const entry = touch(lane.provider, lane.model);
        entry.lanes += lane.kind === "children" ? lane.count ?? 1 : 1;
        if (lane.live) entry.live += 1;
        const lk = laneKey(s.id, lane.id);
        edgeList.push({ id: `${lk}>${modelKey(lane.provider, lane.model)}`, lane: lk, model: modelKey(lane.provider, lane.model), live: lane.live, failed: false });
        const sw = lastSwitch.get(s.id);
        if (sw && lane.active && sw.to_model === lane.model && sw.to_provider === lane.provider) {
          touch(sw.from_provider, sw.from_model);
          edgeList.push({ id: `${lk}>x>${modelKey(sw.from_provider, sw.from_model)}`, lane: lk, model: modelKey(sw.from_provider, sw.from_model), live: false, failed: true });
        }
      }
    }
    const byProvider = new Map<string, { provider: string; models: ModelEntry[]; weight: number }>();
    for (const m of models.values()) {
      let g = byProvider.get(m.provider);
      if (!g) { g = { provider: m.provider, models: [], weight: 0 }; byProvider.set(m.provider, g); }
      g.models.push(m);
      g.weight += m.lanes;
    }
    const sorted = [...byProvider.values()].sort((a, b) => b.weight - a.weight);
    for (const g of sorted) g.models.sort((a, b) => b.lanes - a.lanes);
    return { groups: sorted, edges: edgeList };
  }, [data]);

  // Measure row anchors after layout and on every resize.
  const mapRef = useRef<HTMLDivElement>(null);
  const [anchors, setAnchors] = useState<{ lanes: Map<string, Point>; models: Map<string, Point>; w: number; h: number }>({ lanes: new Map(), models: new Map(), w: 0, h: 0 });
  const measure = useCallback(() => {
    const root = mapRef.current;
    if (!root) return;
    const box = root.getBoundingClientRect();
    const lanes = new Map<string, Point>();
    const models = new Map<string, Point>();
    root.querySelectorAll<HTMLElement>("[data-lane]").forEach((el) => {
      const r = el.getBoundingClientRect();
      lanes.set(el.dataset.lane!, { x: r.right - box.left, y: r.top - box.top + r.height / 2 });
    });
    root.querySelectorAll<HTMLElement>("[data-model]").forEach((el) => {
      const r = el.getBoundingClientRect();
      models.set(el.dataset.model!, { x: r.left - box.left, y: r.top - box.top + r.height / 2 });
    });
    setAnchors({ lanes, models, w: box.width, h: box.height });
  }, []);
  useLayoutEffect(() => { measure(); }, [measure, data, groups]);
  useEffect(() => {
    const root = mapRef.current;
    if (!root) return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(root);
    return () => ro.disconnect();
  }, [measure]);

  const touching = useCallback((edge: Edge) => !hover || edge.lane === hover || edge.model === hover, [hover]);
  const relatedLanes = useMemo(() => new Set(edges.filter(touching).map((e) => e.lane)), [edges, touching]);
  const relatedModels = useMemo(() => new Set(edges.filter(touching).map((e) => e.model)), [edges, touching]);

  const sessions = data?.sessions ?? [];
  const windowLabel = {
    live: text("last 30 minutes", "近 30 分钟"),
    day: text("last 24 hours", "近 24 小时"),
    week: text("last 7 days", "近 7 天"),
  }[dataKey ?? windowKey];

  const pending = data !== null && dataKey !== windowKey;

  return (
    <div className={["flex min-w-0 flex-col gap-4 px-5 pt-4 pb-6 @max-[560px]:px-3", pending && styles.pending].filter(Boolean).join(" ")} aria-busy={pending}>
      <div className={styles.header}>
        <div className="flex min-w-0 flex-[1_1_16rem] items-center gap-3">
          <div className={styles.headerIcon}><Waypoints size={20} strokeWidth={1.75} aria-hidden /></div>
          <div className="min-w-0 flex-1">
            <div className={styles.title}>{text("Overview", "总览")}</div>
            <div className={styles.subtitle}>
              {text("Which session and branch uses which model.", "每个会话、每条分支在用哪个模型。")}
            </div>
          </div>
        </div>
        <Tabs value={windowKey} onValueChange={(next) => setWindowKey(next as WindowKey)}>
          <TabsList aria-label={text("Time window", "时间范围")}>
            <TabsTrigger value="live" className="px-3">{text("Now", "实时")}</TabsTrigger>
            <TabsTrigger value="day" className="px-3">{text("24 hours", "24 小时")}</TabsTrigger>
            <TabsTrigger value="week" className="px-3">{text("7 days", "7 天")}</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      <div className={`${styles.narrow} hidden @max-[560px]:flex`} role="note">
        <PanelLeftClose className={styles.narrowIcon} size={20} strokeWidth={1.75} aria-hidden />
        <div>
          <div className={styles.narrowTitle}>{text("Not enough room for the map", "宽度不够，放不下连接图")}</div>
          <div className={styles.narrowBody}>
            {text(
              "Collapse the sidebars with the panel buttons at the top left, or make the window wider.",
              "用左上角的面板按钮把侧边栏折叠起来，或者把窗口拉宽一些再看。",
            )}
          </div>
        </div>
      </div>

      {data && <div className={`${styles.stats} flex flex-wrap items-baseline gap-x-5 gap-y-1.5`}>
        <Stat value={sessions.filter((s) => s.kind !== "background").length} label={(n) => text(n === 1 ? "session" : "sessions", "个会话")} />
        <Stat value={sessions.filter((s) => s.lanes.some((l) => l.live)).length} label={() => text("answering", "个在回答")} accent />
        <Stat value={data?.stats.requests ?? 0} label={(n) => text(n === 1 ? "model call" : "model calls", "次模型调用")} />
        <Stat value={data?.stats.reroutes ?? 0} label={(n) => text(n === 1 ? "model switch" : "model switches", "次改道")} warn={(data?.stats.reroutes ?? 0) > 0} />
        <span className={styles.statsWindow}>{pending ? text("Loading…", "加载中…") : windowLabel}</span>
      </div>}

      {failed && !data ? (
        <div className={styles.empty}>{text("Couldn't load the overview. Retrying…", "总览加载失败，正在重试…")}</div>
      ) : data && sessions.length === 0 ? (
        <div className={styles.empty}>
          {text(`No session called a model in the ${windowLabel}.`, `${windowLabel}没有会话调用过模型。`)}
        </div>
      ) : (
        <div className={`${styles.map} relative grid grid-cols-[minmax(0,1fr)_clamp(48px,16%,150px)_minmax(0,1fr)] items-start`} ref={mapRef} onMouseLeave={() => setHover(null)}>
          <svg className={styles.edges} width={anchors.w} height={anchors.h} aria-hidden>
            {edges.map((edge) => {
              const a = anchors.lanes.get(edge.lane);
              const b = anchors.models.get(edge.model);
              if (!a || !b) return null;
              const dx = Math.max(24, (b.x - a.x) / 2);
              const d = `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`;
              const cls = [styles.edge, edge.live && styles.edgeLive, edge.failed && styles.edgeFailed, !touching(edge) && styles.edgeDim].filter(Boolean).join(" ");
              return (
                <g key={edge.id} className={cls}>
                  {edge.live && <path d={d} className={styles.edgeHalo} />}
                  <motion.path
                    d={d}
                    className={styles.edgeLine}
                    initial={{ pathLength: 0 }}
                    animate={{ pathLength: 1 }}
                    transition={{ duration: 0.6, ease: [0.165, 0.84, 0.44, 1] }}
                  />
                  {edge.live && (
                    <circle r={3} className={`${styles.edgeDot} motion-reduce:hidden`} style={{ offsetPath: `path("${d}")` }} />
                  )}
                </g>
              );
            })}
          </svg>

          <div className={styles.sessions}>
            {sessions.map((s) => (
              <section key={s.id} className={styles.session + (s.lanes.some((l) => l.live) ? " " + styles.sessionLive : "")}>
                {s.kind === "background" ? (
                  <div className={styles.sessionHead + " " + styles.sessionHeadStatic}>
                    <span className={styles.sessionTitle}>{text("Background calls", "后台调用")}</span>
                    <span className={styles.entry}>{text("No session", "不属于会话")}</span>
                  </div>
                ) : (
                  <button type="button" className={styles.sessionHead} onClick={() => openSession(s.id)}>
                    <span className={styles.sessionTitle}>{s.title || text("Untitled session", "未命名会话")}</span>
                    {entryLabel(s, text) && <span className={styles.entry}>{entryLabel(s, text)}</span>}
                  </button>
                )}
                {s.lanes.map((lane) => {
                  const lk = laneKey(s.id, lane.id);
                  return (
                    <div
                      key={lk}
                      data-lane={lk}
                      className={styles.lane + (lane.live ? " " + styles.laneLive : "") + (hover && !relatedLanes.has(lk) ? " " + styles.dim : "")}
                      onMouseEnter={() => setHover(lk)}
                    >
                      <LaneIcon kind={lane.kind} />
                      <span className={styles.laneName}>{laneName(lane, text)}</span>
                      <span className={styles.laneState}>
                        {lane.live ? (
                          <><span className={styles.pulse} aria-hidden />{text("Answering", "回答中")}</>
                        ) : (
                          ago(data!.now, lane.updated_at, text)
                        )}
                      </span>
                    </div>
                  );
                })}
                {s.hidden_branches > 0 && (
                  <div className={styles.more}>{text(s.hidden_branches === 1 ? "1 more branch" : `${s.hidden_branches} more branches`, `另有 ${s.hidden_branches} 条分支`)}</div>
                )}
              </section>
            ))}
            {(data?.idle_sessions ?? 0) > 0 && (
              <div className={`${styles.idle} pl-1`}>
                {text(
                  data!.idle_sessions === 1 ? "1 other session is idle" : `${data!.idle_sessions} other sessions are idle`,
                  `另外 ${data!.idle_sessions} 个会话空闲`,
                )}
              </div>
            )}
          </div>

          <div className={styles.gutter} />

          <div className={styles.models}>
            {groups.map((g) => (
              <section key={g.provider} className={styles.group}>
                <button type="button" className={styles.groupHead} onClick={() => g.provider && onSelectProvider(g.provider)}>
                  <ProviderIcon id={g.provider} size={16} />
                  <span className={styles.groupName}>{labels.get(g.provider) || g.provider || text("Unknown", "未知")}</span>
                  <AccountChip accounts={data?.accounts[g.provider] ?? []} />
                </button>
                {g.models.map((m) => {
                  const mk = modelKey(m.provider, m.model);
                  const failure = (data?.events ?? []).find((e) => e.from_provider === m.provider && e.from_model === m.model);
                  return (
                    <div
                      key={mk}
                      data-model={mk}
                      className={styles.model + (m.live ? " " + styles.modelLive : "") + (failure ? " " + styles.modelWarn : "") + (hover && !relatedModels.has(mk) ? " " + styles.dim : "")}
                      onMouseEnter={() => setHover(mk)}
                    >
                      <span className={styles.modelName}>{m.model || text("Unknown model", "未知模型")}</span>
                      <span className={styles.modelState}>
                        {failure
                          ? `${reasonLabel(failure.reason, text)} · ${ago(data!.now, failure.ts, text)}`
                          : m.live
                            ? text(`${m.live} answering`, `${m.live} 个在回答`)
                            : text(`${m.lanes} in use`, `${m.lanes} 处在用`)}
                      </span>
                    </div>
                  );
                })}
              </section>
            ))}
          </div>
        </div>
      )}

      {(data?.events.length ?? 0) > 0 && (
        <div className={styles.switches}>
          <div className={styles.switchesTitle}>{text("Recent model switches", "最近的改道")}</div>
          {data!.events.map((e, i) => (
            <div key={i} className={styles.switchRow}>
              <span className={styles.reason}>{reasonLabel(e.reason, text)}</span>
              <span className={styles.switchText}>
                {e.session_title ? <b>{e.session_title}</b> : null}
                {e.session_title ? " · " : ""}
                {`${labels.get(e.from_provider) || e.from_provider} ${e.from_model}`}
                <span className={styles.arrow}>→</span>
                {`${labels.get(e.to_provider) || e.to_provider} ${e.to_model}`}
              </span>
              <span className={styles.switchTime}>{ago(data!.now, e.ts, text)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

type Text = (en: string, zh: string) => string;

function Stat({ value, label, accent, warn }: { value: number; label: (n: number) => string; accent?: boolean; warn?: boolean }) {
  return (
    <span className={styles.stat + (accent && value > 0 ? " " + styles.statAccent : "") + (warn ? " " + styles.statWarn : "")}>
      <span className={styles.statValue}>{value.toLocaleString()}</span>
      <span className={styles.statLabel}>{label(value)}</span>
    </span>
  );
}

function LaneIcon({ kind }: { kind: LaneKind }) {
  const Icon = kind === "sub_agent" ? Bot : kind === "children" ? Layers : kind === "calls" || kind === "background" ? Code2 : GitBranch;
  return <Icon className={styles.laneIcon} size={14} strokeWidth={1.75} aria-hidden />;
}

function laneName(lane: Lane, text: Text): string {
  if (lane.kind === "children") return text(lane.count === 1 ? "1 child session" : `${lane.count ?? 0} child sessions`, `${lane.count ?? 0} 个子会话`);
  if (lane.kind === "calls") return text("Program calls", "程序调用");
  if (lane.kind === "background") return text(lane.events === 1 ? "1 call" : `${lane.events ?? 0} calls`, `${lane.events ?? 0} 次调用`);
  if (lane.name) return lane.name;
  if (lane.trunk) return "main";
  return lane.kind === "sub_agent" ? text("Sub-agent", "子 Agent") : lane.id.slice(0, 8);
}

const CHANNELS: Record<string, [string, string]> = {
  wechat: ["WeChat", "微信"],
  telegram: ["Telegram", "Telegram"],
  discord: ["Discord", "Discord"],
  slack: ["Slack", "Slack"],
};

function entryLabel(s: SessionRow, text: Text): string {
  const channel = CHANNELS[s.channel];
  if (channel) return text(channel[0], channel[1]);
  if (s.source === "agent") return "Agent";
  return "";
}

function reasonLabel(reason: string, text: Text): string {
  switch (reason) {
    case "rate_limit": return text("Rate limited", "限流");
    case "overloaded": return text("Overloaded", "过载");
    case "server": return text("Server error", "服务出错");
    case "timeout": return text("Timed out", "超时");
    case "network": return text("Network", "网络");
    default: return reason;
  }
}

function ago(now: number, ts: number, text: Text): string {
  const s = Math.max(0, Math.round(now - ts));
  if (s < 60) return text("just now", "刚刚");
  const m = Math.round(s / 60);
  if (m < 60) return text(`${m} min ago`, `${m} 分钟前`);
  const h = Math.round(m / 60);
  if (h < 48) return text(`${h} h ago`, `${h} 小时前`);
  return text(`${Math.round(h / 24)} d ago`, `${Math.round(h / 24)} 天前`);
}

function AccountChip({ accounts }: { accounts: Account[] }) {
  const { text } = useTranslation();
  if (accounts.length === 0) return null;
  const broken = accounts.filter((a) => ["needs_reauth", "billing_blocked", "revoked", "invalid"].includes(a.status));
  const cooling = accounts.filter((a) => a.until_ms > 0);
  if (broken.length > 0) {
    return <span className={styles.chip + " " + styles.chipError}>{text(`${broken.length} key needs attention`, `${broken.length} 个 key 需要处理`)}</span>;
  }
  if (cooling.length > 0) {
    const until = new Date(Math.min(...cooling.map((a) => a.until_ms)));
    const hhmm = until.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return <span className={styles.chip + " " + styles.chipWarn}>{text(`Rate limited · back at ${hhmm}`, `限流 · ${hhmm} 恢复`)}</span>;
  }
  if (accounts.length > 1) {
    return <span className={styles.chip}>{text(`${accounts.length} keys`, `${accounts.length} 个 key`)}</span>;
  }
  return null;
}
