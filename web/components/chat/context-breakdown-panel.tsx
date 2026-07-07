"use client";

/**
 * /context 面板 —— 当前会话一次 LLM 调用的 input token 分类分解，
 * 对齐 Claude Code 的 /context：Messages / System / Tools(loaded) /
 * Tools(deferred) + per-tool 明细。
 *
 * 数据来自 GET /api/sessions/{id}/context（后端现算，不落库 —— 存储铁律）。
 * 点 ContextBadge 弹出，随时可看当前会话的 context 怎么构成。
 */
import { useEffect, useMemo, useState } from "react";
import { X, ChevronRight, ChevronDown } from "lucide-react";
import { useTranslation } from "@/lib/i18n";

interface ToolItem {
  name: string;
  tokens: number;
  deferred: boolean;
}

interface Breakdown {
  messages?: number;
  system_prompt?: number;
  skills?: number;
  memory?: number;
  tools_schema?: number;
  tools_deferred_catalog?: number;
  mcp_tools?: number;
  input_used?: number;
  input_used_pct?: number;
  tools?: ToolItem[];
  context_window?: number;
  model?: string;
  error?: string;
}

interface Props {
  sessionId: string | null;
  onClose: () => void;
}

const COLORS = [
  "var(--accent-blue)",
  "var(--accent-green)",
  "var(--accent-orange, #e08a3c)",
  "var(--accent-purple, #9a6fd0)",
  "var(--text-muted)",
];

function fmt(n: number): string {
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return String(n);
}

export function ContextBreakdownPanel({ sessionId, onClose }: Props) {
  const { text } = useTranslation();
  const [data, setData] = useState<Breakdown | null>(null);
  const [loading, setLoading] = useState(true);
  const [toolsOpen, setToolsOpen] = useState(false);

  useEffect(() => {
    if (!sessionId) return;
    setLoading(true);
    fetch(`/api/sessions/${encodeURIComponent(sessionId)}/context`)
      .then((r) => r.json())
      .then((d) => setData(d))
      .catch((e) => setData({ error: String(e) }))
      .finally(() => setLoading(false));
  }, [sessionId]);

  const rows = useMemo(() => {
    if (!data) return [];
    const win = data.context_window || 0;
    const pct = (v: number) => (win > 0 ? (v / win) * 100 : 0);
    const defs: [string, string, number][] = [
      [text("Messages", "对话消息"), "messages", data.messages || 0],
      [text("System prompt", "系统提示"), "system", data.system_prompt || 0],
      [text("Tools (loaded)", "工具(已加载)"), "tools", data.tools_schema || 0],
      [
        text("Tools (deferred)", "工具(延迟)"),
        "deferred",
        data.tools_deferred_catalog || 0,
      ],
    ];
    return defs
      .filter(([, , v]) => v > 0)
      .map(([label, , v], i) => ({
        label,
        tokens: v,
        pct: pct(v),
        color: COLORS[i % COLORS.length],
      }));
  }, [data, text]);

  const usedPct = data?.input_used_pct != null ? data.input_used_pct * 100 : 0;

  return (
    <aside
      className="flex h-screen w-[360px] shrink-0 flex-col border-l"
      style={{ background: "var(--bg-secondary)", borderColor: "var(--border)" }}
    >
      <div
        className="flex h-12 items-center justify-between border-b px-4"
        style={{ borderColor: "var(--border)" }}
      >
        <h3
          className="text-[13px] font-semibold"
          style={{ color: "var(--text-bright)" }}
        >
          {text("Context", "上下文构成")}
        </h3>
        <button onClick={onClose}>
          <X className="h-4 w-4" style={{ color: "var(--text-muted)" }} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {loading ? (
          <div
            className="p-6 text-center text-[12px]"
            style={{ color: "var(--text-muted)" }}
          >
            {text("Loading…", "加载中…")}
          </div>
        ) : data?.error ? (
          <div
            className="p-4 text-[12px]"
            style={{ color: "var(--accent-red)" }}
          >
            {data.error}
          </div>
        ) : (
          <>
            {/* 顶部：总用量条 */}
            <div className="mb-4">
              <div
                className="mb-1 flex justify-between text-[12px]"
                style={{ color: "var(--text-primary)" }}
              >
                <span>{text("Context window", "上下文窗口")}</span>
                <span>
                  {fmt(data?.input_used || 0)} / {fmt(data?.context_window || 0)}{" "}
                  ({usedPct.toFixed(1)}%)
                </span>
              </div>
              <div
                className="h-2 w-full overflow-hidden rounded"
                style={{ background: "var(--bg-tertiary)" }}
              >
                <div
                  className="h-full"
                  style={{
                    width: `${Math.min(usedPct, 100)}%`,
                    background: "var(--accent-blue)",
                  }}
                />
              </div>
            </div>

            {/* 分类行 */}
            <div className="space-y-2">
              {rows.map((r) => (
                <div key={r.label} className="text-[12px]">
                  <div className="mb-0.5 flex items-center justify-between">
                    <span className="flex items-center gap-2">
                      <span
                        className="h-2 w-2 rounded-sm"
                        style={{ background: r.color }}
                      />
                      <span style={{ color: "var(--text-primary)" }}>
                        {r.label}
                      </span>
                    </span>
                    <span style={{ color: "var(--text-muted)" }}>
                      {fmt(r.tokens)} · {r.pct.toFixed(1)}%
                    </span>
                  </div>
                </div>
              ))}
            </div>

            {/* per-tool 展开 */}
            {data?.tools && data.tools.length > 0 && (
              <div className="mt-4">
                <button
                  onClick={() => setToolsOpen(!toolsOpen)}
                  className="flex w-full items-center gap-1 text-[12px]"
                  style={{ color: "var(--text-primary)" }}
                >
                  {toolsOpen ? (
                    <ChevronDown className="h-3 w-3" />
                  ) : (
                    <ChevronRight className="h-3 w-3" />
                  )}
                  <span>
                    {text("Per-tool", "各工具")} ({data.tools.length})
                  </span>
                </button>
                {toolsOpen && (
                  <div className="mt-2 space-y-1">
                    {[...data.tools]
                      .sort((a, b) => b.tokens - a.tokens)
                      .map((t) => (
                        <div
                          key={t.name}
                          className="flex items-center justify-between text-[11px] font-mono"
                          style={{
                            color: t.deferred
                              ? "var(--text-muted)"
                              : "var(--text-primary)",
                          }}
                        >
                          <span className="truncate">
                            {t.name}
                            {t.deferred && (
                              <span
                                className="ml-1"
                                style={{ color: "var(--text-muted)" }}
                              >
                                (deferred)
                              </span>
                            )}
                          </span>
                          <span style={{ color: "var(--text-muted)" }}>
                            {t.tokens}
                          </span>
                        </div>
                      ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
