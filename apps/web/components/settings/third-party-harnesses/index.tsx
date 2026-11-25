"use client";

// Settings → Third-Party Harnesses: Claude Code and Codex sessions on this
// computer, read from their own files by the Worker
// (openprogram/models/usage/harnesses, GET /api/usage/harnesses). Kept apart
// from Token Usage so their cost never mixes with OpenProgram's own.

import { useEffect, useMemo, useState } from "react";
import { Blocks } from "lucide-react";

import { SettingsPage } from "../settings-ui";
import { SchemaSettings } from "../sections/schema-settings";
import usage from "../token-usage/usage.module.css";
import { catColor, fmtCost, fmtNum } from "../token-usage";
import { StackedBarChart, type StackPoint } from "../token-usage/stacked-bar-chart";
import { ProviderIcon } from "../providers/icons/provider-icon";
import local from "./harnesses.module.css";
import { HarnessModels } from "./harness-models";
import { Button } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/copy-button";
import { ManageEmptyState, ManageSubnav } from "@/components/ui/manage-page";
import { SearchInput } from "@/components/ui/search-input";
import { HoverTip } from "@/components/ui/tooltip";
import { refreshSettings, useSettingsData } from "@/lib/prefs/settings-cache";
import { HARNESSES_URL } from "@/lib/settings/settings-urls";
import { useTranslation } from "@/lib/i18n";

type Totals = {
  calls: number;
  input_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost: number;
  unpriced_calls: number;
};

type AgentRow = Totals & { agent: string; label: string; sessions: number };
type ModelRow = Totals & { agent: string; model: string };
type ClientRow = Totals & { agent: string; client: string };
type Session = Totals & {
  agent: string;
  id: string;
  title: string;
  first_prompt: string;
  cwd: string;
  branch: string;
  client: string;
  models: string[];
  first_ts: number;
  last_ts: number;
  resume: string;
};
type Quota = {
  agent: string;
  observed_at: number;
  plan: string | null;
  windows: { used_percent: number; window_minutes: number | null; resets_at: number | null }[];
  credits: number | null;
};
type Report = {
  enabled: boolean;
  agents: AgentRow[];
  by_model: ModelRow[];
  by_client: ClientRow[];
  trend: { day: string; agents: Record<string, { total_tokens: number; cost: number }> }[];
  quota: Quota[];
  sessions: Session[];
};

const DAYS = 30;
const SHOWN = 20;
/** Re-read while the page is open: the Worker reads only what was appended. */
const REREAD_MS = 10_000;

/** Each harness's mark: the maker whose models it runs. */
const ICON: Record<string, string> = { claude_code: "anthropic", codex: "openai" };

function cost(value: number, unpriced: number): string {
  return fmtCost(value) + (unpriced > 0 ? "+" : "");
}

function folder(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? "";
}

function dayStart(day: string): number {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d).getTime() / 1000;
}

export function ThirdPartyHarnessesSection() {
  const { text } = useTranslation();
  const data = useSettingsData<Report>(HARNESSES_URL);
  const [agent, setAgent] = useState("");
  const [query, setQuery] = useState("");
  const [all, setAll] = useState(false);

  useEffect(() => {
    const timer = window.setInterval(() => void refreshSettings(HARNESSES_URL), REREAD_MS);
    return () => window.clearInterval(timer);
  }, []);

  const order = useMemo(() => (data?.agents ?? []).map((a) => a.agent), [data]);
  const color = (id: string) => catColor(Math.max(0, order.indexOf(id)));
  const label = (id: string) => data?.agents.find((a) => a.agent === id)?.label ?? id;

  const clientName = (client: string) => ({
    "cli": text("Terminal", "终端"),
    "claude-desktop": text("Claude desktop app", "Claude 桌面版"),
    "claude-vscode": "VS Code",
    "sdk-ts": text("SDK, another app", "SDK（其他应用）"),
    "sdk-py": text("SDK, another app", "SDK（其他应用）"),
    "Codex Desktop": text("Codex desktop app", "Codex 桌面版"),
    "codex_cli_rs": text("Terminal", "终端"),
    "codex_exec": "codex exec",
    "codex_vscode": "VS Code",
  }[client] ?? client);

  const points = useMemo<StackPoint[]>(() => {
    const byDay = new Map((data?.trend ?? []).map((d) => [d.day, d.agents]));
    const today = new Date();
    return Array.from({ length: DAYS }, (_, i) => {
      const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - (DAYS - 1 - i));
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      const day = byDay.get(key) ?? {};
      return { ts: dayStart(key), values: order.map((id) => day[id]?.total_tokens ?? 0) };
    });
  }, [data, order]);

  const sessions = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data?.sessions ?? []).filter((s) =>
      (!agent || s.agent === agent)
      && (!q || [s.title, s.first_prompt, s.cwd, s.branch, ...s.models].some((v) => v.toLowerCase().includes(q))));
  }, [data, agent, query]);

  const ago = (ts: number) => {
    const s = Date.now() / 1000 - ts;
    if (s < 3600) return text(`${Math.max(1, Math.round(s / 60))}m ago`, `${Math.max(1, Math.round(s / 60))} 分钟前`);
    if (s < 86400) return text(`${Math.round(s / 3600)}h ago`, `${Math.round(s / 3600)} 小时前`);
    return text(`${Math.round(s / 86400)}d ago`, `${Math.round(s / 86400)} 天前`);
  };
  const when = (ts: number) => new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const estimateTip = text("At API list prices, not your subscription bill", "按 API 价格估算，不是订阅账单");

  const empty = data && data.agents.every((a) => a.calls === 0);
  const models = (data?.by_model ?? []).filter((r) => !agent || r.agent === agent);
  const clients = (data?.by_client ?? []).filter((r) => !agent || r.agent === agent);

  return (
    <SettingsPage
      title={text("Third-Party Harnesses", "第三方 Harness")}
      description={text(
        "The models Claude Code and Codex on this computer run on, and their sessions, read from their own files.",
        "本机 Claude Code 和 Codex 使用的模型，以及从它们自己的文件读取的会话。",
      )}
      bodyClassName={usage.usagePage}
    >
      <HarnessModels />
      {data && !data.enabled && (
        <ManageEmptyState
          compact
          icon={<Blocks />}
          title={text("Reading is turned off", "已关闭读取")}
          description={text(
            "Turn on reading below to see their sessions here.",
            "打开下方的读取开关后，这里会显示它们的会话。",
          )}
        />
      )}
      {data && data.enabled && empty && (
        <ManageEmptyState
          compact
          icon={<Blocks />}
          title={text("No Claude Code or Codex sessions", "没有 Claude Code 或 Codex 会话")}
          description={text(
            `Sessions from the last ${DAYS} days in ~/.claude and ~/.codex show here.`,
            `~/.claude 和 ~/.codex 里最近 ${DAYS} 天的会话会显示在这里。`,
          )}
        />
      )}
      {data && data.enabled && !empty && (
        <>
          <h3 className={usage.sectionTitle}>{text(`Last ${DAYS} days`, `最近 ${DAYS} 天`)}</h3>
          <div className={local.agents}>
            {data.agents.map((a) => {
              const quota = data.quota.find((q) => q.agent === a.agent);
              return (
                <div key={a.agent} className={usage.card}>
                  <div className={local.agentHead}>
                    <ProviderIcon id={ICON[a.agent] ?? a.agent} size={20} />
                    <span className={local.agentName}>{a.label}</span>
                    <span className={local.agentMeta}>{text(`${a.sessions} sessions`, `${a.sessions} 个会话`)}</span>
                  </div>
                  <div className={local.agentNums}>
                    <div><div className={usage.cardValue}>{fmtNum(a.total_tokens)}</div><div className={usage.cardLabel}>{text("Tokens", "Token")}</div></div>
                    <div><div className={usage.cardValue}>{fmtNum(a.output_tokens)}</div><div className={usage.cardLabel}>{text("Output", "输出")}</div></div>
                    <div>
                      <HoverTip label={estimateTip}><div className={usage.cardValue} tabIndex={0}>{cost(a.cost, a.unpriced_calls)}</div></HoverTip>
                      <div className={usage.cardLabel}>
                        {text("API estimate", "按 API 价估算")}
                        {a.unpriced_calls > 0 && <span className={usage.cardNote}> · {text(`${a.unpriced_calls} calls unpriced`, `${a.unpriced_calls} 次无价格`)}</span>}
                      </div>
                    </div>
                  </div>
                  {quota?.windows.map((w, i) => (
                    <div key={i} className={local.quota}>
                      <div className={local.quotaRow}>
                        <span>
                          {w.window_minutes && w.window_minutes >= 10080 ? text("Weekly limit", "每周额度")
                            : w.window_minutes && w.window_minutes <= 300 ? text("5-hour limit", "5 小时额度")
                            : text("Plan limit", "套餐额度")}
                          {quota.plan ? ` · ${quota.plan}` : ""}
                        </span>
                        <span>{text(`${Math.round(w.used_percent)}% used`, `已用 ${Math.round(w.used_percent)}%`)}</span>
                      </div>
                      <div className={local.meter} role="meter" aria-valuenow={w.used_percent} aria-valuemin={0} aria-valuemax={100}>
                        <i style={{ width: `${Math.min(100, w.used_percent)}%` }} />
                      </div>
                      <div className={usage.cardLabel}>
                        {w.resets_at ? text(`Resets ${when(w.resets_at)}`, `${when(w.resets_at)} 重置`) + " · " : ""}
                        {quota.credits != null && i === 0 ? text(`Credits ${Math.round(quota.credits).toLocaleString()}`, `Credits ${Math.round(quota.credits).toLocaleString()}`) + " · " : ""}
                        {text(`as of ${when(quota.observed_at)}`, `截至 ${when(quota.observed_at)}`)}
                      </div>
                    </div>
                  ))}
                  {!quota && a.agent === "claude_code" && (
                    <div className={usage.cardLabel}>{text("Claude Code keeps no plan limits in its files.", "Claude Code 的文件里没有套餐额度。")}</div>
                  )}
                </div>
              );
            })}
          </div>

          <div className={usage.section}>
            <h3 className={usage.sectionTitle}>{text("Daily tokens", "每日 Token")}</h3>
            <StackedBarChart
              points={points}
              colors={order.map(color)}
              format={(v) => fmtNum(Math.round(v))}
              formatTip={(v) => `${fmtNum(v)} tok`}
            />
            <div className={usage.legend}>
              {order.map((id) => (
                <span key={id} className="inline-flex items-center gap-[5px]">
                  <span className={usage.legendDot} style={{ background: color(id) }} />
                  {label(id)}
                </span>
              ))}
            </div>
          </div>

          <ManageSubnav
            className={usage.dimSubnav}
            tabs={[{ id: "", label: text("All", "全部") }, ...data.agents.map((a) => ({ id: a.agent, label: a.label }))]}
            activeTab={agent}
            onTabChange={(id) => { setAgent(id); setAll(false); }}
            ariaLabel={text("Harness", "Harness")}
            panelId="harness-sessions"
            end={<SearchInput value={query} onChange={setQuery} placeholder={text("Search sessions", "搜索会话")} className={local.search} />}
          />

          <div className={usage.section} id="harness-sessions" role="tabpanel">
            <h3 className={usage.sectionTitle}>{text("Sessions", "会话")}</h3>
            {sessions.length === 0 ? (
              <div className={usage.muted}>{text("No sessions match.", "没有匹配的会话。")}</div>
            ) : (
              <div className={local.sessions}>
                {(all ? sessions : sessions.slice(0, SHOWN)).map((s) => (
                  <div key={`${s.agent}:${s.id}`} className={local.session}>
                    <ProviderIcon id={ICON[s.agent] ?? s.agent} size={18} />
                    <div className={local.sessionMain}>
                      <div className={local.sessionTitle}>{s.title || text("Untitled session", "未命名会话")}</div>
                      <div className={local.sessionMeta}>
                        {[folder(s.cwd), s.branch, s.models.slice(0, 2).join(", "), clientName(s.client), ago(s.last_ts)].filter(Boolean).join(" · ")}
                      </div>
                    </div>
                    <div className={`${local.sessionNum} ${local.sessionTokens}`}>
                      {fmtNum(s.total_tokens)}
                      <div className={local.sessionMeta}>{text(`${s.calls.toLocaleString()} calls`, `${s.calls.toLocaleString()} 次调用`)}</div>
                    </div>
                    <div className={local.sessionNum}>{cost(s.cost, s.unpriced_calls)}</div>
                    <CopyButton value={s.resume} label={text("Copy the command that continues it", "复制继续这个会话的命令")} size="icon-sm" />
                  </div>
                ))}
              </div>
            )}
            {!all && sessions.length > SHOWN && (
              <Button variant="ghost" size="sm" className={local.more} onClick={() => setAll(true)}>
                {text(`Show all ${sessions.length}`, `显示全部 ${sessions.length} 个`)}
              </Button>
            )}
          </div>

          {models.length > 0 && (
            <div className={usage.section}>
              <h3 className={usage.sectionTitle}>{text("By model", "按模型")}</h3>
              <div className="overflow-x-auto">
                <table className={usage.table}>
                  <thead>
                    <tr>
                      <th>{text("Model", "模型")}</th>
                      <th>{text("Harness", "Harness")}</th>
                      <th className={usage.num}>{text("Input", "输入")}</th>
                      <th className={usage.num}>{text("Cache read", "缓存读取")}</th>
                      <th className={usage.num}>{text("Output", "输出")}</th>
                      <th className={usage.num}>{text("Calls", "调用")}</th>
                      <th className={usage.num}>{text("API estimate", "按 API 价估算")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {models.map((r) => (
                      <tr key={`${r.agent}:${r.model}`}>
                        <td className={usage.modelCell}>{r.model}</td>
                        <td>{label(r.agent)}</td>
                        <td className={usage.num}>{fmtNum(r.input_tokens + r.cache_write_tokens)}</td>
                        <td className={usage.num}>{fmtNum(r.cache_read_tokens)}</td>
                        <td className={usage.num}>{fmtNum(r.output_tokens)}</td>
                        <td className={usage.num}>{fmtNum(r.calls)}</td>
                        <td className={usage.num}>
                          {r.unpriced_calls === r.calls ? text("Unpriced", "无价格") : cost(r.cost, r.unpriced_calls)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {clients.length > 0 && (
            <div className={usage.section}>
              <h3 className={usage.sectionTitle}>{text("Started from", "启动来源")}</h3>
              <div className="overflow-x-auto">
                <table className={usage.table}>
                  <thead>
                    <tr>
                      <th>{text("App", "应用")}</th>
                      <th>{text("Harness", "Harness")}</th>
                      <th className={usage.num}>{text("Calls", "调用")}</th>
                      <th className={usage.num}>{text("Tokens", "Token")}</th>
                      <th className={usage.num}>{text("API estimate", "按 API 价估算")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {clients.map((r) => (
                      <tr key={`${r.agent}:${r.client}`}>
                        <td className={usage.modelCell}>{clientName(r.client) || "—"}</td>
                        <td>{label(r.agent)}</td>
                        <td className={usage.num}>{fmtNum(r.calls)}</td>
                        <td className={usage.num}>{fmtNum(r.total_tokens)}</td>
                        <td className={usage.num}>{cost(r.cost, r.unpriced_calls)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
      <SchemaSettings group="Harnesses" />
    </SettingsPage>
  );
}
