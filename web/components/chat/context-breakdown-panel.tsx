"use client";

/**
 * /context 面板 —— 当前会话的 input token 分类分解，对齐 Claude Code /context：
 * 总览（System prompt / System tools loaded+deferred / MCP / Memory / Skills /
 * Messages / Free space）+ Skills / Memory / MCP 各自的明细列表。
 *
 * 数据来自 GET /api/sessions/{id}/context（后端现算，不落库 —— 存储铁律）。
 */
import React, { useEffect, useMemo, useState } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";
import { useTranslation } from "@/lib/i18n";

interface ToolItem {
  name: string;
  tokens: number;
  deferred: boolean;
}
interface SkillItem {
  name: string;
  source: string;
  tokens: number;
}
interface MemItem {
  path: string;
  tokens: number;
}
interface McpItem {
  server: string;
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
  mcp_tools_deferred?: number;
  input_used?: number;
  free_space?: number;
  tools?: ToolItem[];
  skills_detail?: SkillItem[];
  memory_detail?: MemItem[];
  mcp_detail?: McpItem[];
  context_window?: number;
  model?: string;
  error?: string;
}

interface Props {
  sessionId: string | null;
  /** 当前分支头（DAG 选中的分支）。传入则按该分支算上下文；切分支时变化
   *  会触发重新拉取。缺省时后端回退会话全局 head。*/
  headId?: string | null;
  /** 保留兼容旧调用；面板本身不再渲染关闭按钮（点外面即关）。*/
  onClose?: () => void;
}

function fmt(n: number): string {
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return String(n);
}

const CAT_COLORS: Record<string, string> = {
  system: "#8a94a6",
  tools: "#5aa469",
  deferred: "#c98a3c",
  mcp: "#4a90d9",
  memory: "#c07ba0",
  skills: "#b08a3c",
  messages: "#a06fd0",
  free: "#3a3f47",
};

// Row / Section 提到模块级（不再定义在组件体内）—— 否则每次组件 render 都会
// 把它们当成全新组件类型，导致 59+54+24 个子行整棵子树卸载重挂而非 diff 更新，
// 这正是"弹出时卡"的根因。这里它们只依赖 props，纯函数组件，可安全上提。
const Row = React.memo(function Row({
  name,
  tokens,
  dim,
}: {
  name: string;
  tokens?: number;
  dim?: boolean;
}) {
  return (
    <div
      className="flex items-center justify-between py-0.5 text-[11px] font-mono"
      style={{ color: dim ? "var(--text-muted)" : "var(--text-primary)" }}
    >
      <span className="truncate">{name}</span>
      {tokens != null && (
        <span className="ml-2 shrink-0" style={{ color: "var(--text-muted)" }}>
          {fmt(tokens)}
        </span>
      )}
    </div>
  );
});

function Section({
  title,
  count,
  open,
  onToggle,
  children,
}: {
  title: string;
  count: number;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  if (count <= 0) return null;
  return (
    <div className="mt-3">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-1 text-[12px] font-semibold"
        style={{ color: "var(--text-primary)" }}
      >
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        <span>
          {title} ({count})
        </span>
      </button>
      {open && <div className="mt-1 pl-4">{children}</div>}
    </div>
  );
}

export function ContextBreakdownPanel({ sessionId, headId }: Props) {
  const { text } = useTranslation();
  const [data, setData] = useState<Breakdown | null>(null);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!sessionId) return;
    setLoading(true);
    const qs = headId ? `?head_id=${encodeURIComponent(headId)}` : "";
    fetch(`/api/sessions/${encodeURIComponent(sessionId)}/context${qs}`)
      .then((r) => r.json())
      .then((d) => setData(d))
      .catch((e) => setData({ error: String(e) }))
      .finally(() => setLoading(false));
  }, [sessionId, headId]);

  const win = data?.context_window || 0;
  const pct = (v: number) => (win > 0 ? (v / win) * 100 : 0);

  const rows = useMemo(() => {
    if (!data) return [];
    const defs: [string, string, number][] = [
      [text("System prompt", "系统提示"), "system", data.system_prompt || 0],
      [text("System tools", "工具"), "tools", data.tools_schema || 0],
      [
        text("System tools (deferred)", "工具(延迟)"),
        "deferred",
        data.tools_deferred_catalog || 0,
      ],
      [text("MCP tools", "MCP 工具"), "mcp", data.mcp_tools || 0],
      [
        text("MCP tools (deferred)", "MCP 工具(延迟)"),
        "deferred",
        data.mcp_tools_deferred || 0,
      ],
      [text("Memory files", "记忆文件"), "memory", data.memory || 0],
      [text("Skills", "技能"), "skills", data.skills || 0],
      [text("Messages", "对话消息"), "messages", data.messages || 0],
      [text("Free space", "空闲"), "free", data.free_space || 0],
    ];
    // 全部分类都显示（含 0），不过滤 —— 让用户看到每一档存在与否。
    return defs.map(([label, key, v]) => ({
      label,
      tokens: v,
      pct: pct(v),
      color: CAT_COLORS[key] || "#888",
      zero: v <= 0,
    }));
  }, [data, text]);

  const usedPct =
    win > 0 ? (((data?.input_used || 0) + (win - (data?.free_space ?? win))) / win) : 0;
  const totalUsed = win - (data?.free_space ?? 0);

  const toggle = (k: string) => setOpen((o) => ({ ...o, [k]: !o[k] }));

  return (
    <div
      className="flex max-h-[70vh] w-[380px] flex-col overflow-hidden border"
      style={{
        // 背景用 --bg-tertiary（比页面背景亮一点的灰，对齐左侧 plus 菜单）。
        // 圆角 16px = 底部圆形按钮的半径（--composer-button-size 32 / 2），
        // 让卡片右下圆角弧度与那排圆形按钮/圆环一致。阴影用统一 popover 阴影。
        background: "var(--bg-tertiary)",
        borderColor: "var(--border)",
        borderRadius: 16,
        boxShadow: "var(--shadow-popover)",
      }}
    >
      <div className="flex-1 overflow-y-auto" style={{ padding: "16px 16px 16px" }}>
        {loading ? (
          <div className="p-6 text-center text-[12px]" style={{ color: "var(--text-muted)" }}>
            {text("Loading…", "加载中…")}
          </div>
        ) : data?.error ? (
          <div className="p-4 text-[12px]" style={{ color: "var(--accent-red)" }}>
            {data.error}
          </div>
        ) : (
          <>
            {/* 顶部总用量 —— 作为唯一的标题行（去掉了单独的 Context 标题），
                字号加大以充当标题。*/}
            <div className="mb-1.5 flex items-baseline justify-between">
              <span
                className="text-[13px] font-semibold"
                style={{ color: "var(--text-primary)" }}
              >
                {text("Context window", "上下文窗口")}
              </span>
              <span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>
                {fmt(totalUsed)} / {fmt(win)} ({(usedPct * 100).toFixed(1)}%)
              </span>
            </div>
            <div className="mb-1 flex h-2 w-full overflow-hidden rounded" style={{ background: CAT_COLORS.free }}>
              {rows
                .filter((r) => r.label !== text("Free space", "空闲"))
                .map((r) => (
                  <div key={r.label} style={{ width: `${r.pct}%`, background: r.color }} />
                ))}
            </div>

            {/* 分类总览 */}
            <div className="mt-3 space-y-1.5">
              {rows.map((r) => (
                <div
                  key={r.label}
                  className="flex items-center justify-between text-[12px]"
                  style={{ opacity: r.zero ? 0.4 : 1 }}
                >
                  <span className="flex items-center gap-2">
                    <span className="h-2 w-2 rounded-sm" style={{ background: r.color }} />
                    <span style={{ color: "var(--text-primary)" }}>{r.label}</span>
                  </span>
                  <span style={{ color: "var(--text-muted)" }}>
                    {fmt(r.tokens)} · {r.pct.toFixed(1)}%
                  </span>
                </div>
              ))}
            </div>

            <div className="my-3 border-t" style={{ borderColor: "var(--border)" }} />

            {/* Per-tool */}
            <Section
              open={!!open["tools"]}
              onToggle={() => toggle("tools")}
              title={text("Per-tool", "各工具")}
              count={data?.tools?.length || 0}
            >
              {[...(data?.tools || [])]
                .sort((a, b) => b.tokens - a.tokens)
                .map((t) => (
                  <Row key={t.name} name={t.deferred ? `${t.name} (deferred)` : t.name} tokens={t.tokens} dim={t.deferred} />
                ))}
            </Section>

            {/* MCP tools */}
            <Section
              open={!!open["mcp"]}
              onToggle={() => toggle("mcp")}
              title={text("MCP tools", "MCP 工具")}
              count={data?.mcp_detail?.length || 0}
            >
              {[...(data?.mcp_detail || [])]
                .sort((a, b) => b.tokens - a.tokens)
                .map((m) => (
                  <Row
                    key={m.name}
                    name={m.deferred ? `${m.name} (deferred)` : m.name}
                    tokens={m.tokens}
                    dim={m.deferred}
                  />
                ))}
            </Section>

            {/* Memory */}
            <Section
              open={!!open["memory"]}
              onToggle={() => toggle("memory")}
              title={text("Memory files", "记忆文件")}
              count={data?.memory_detail?.length || 0}
            >
              {(data?.memory_detail || []).map((m) => (
                <Row key={m.path} name={m.path} tokens={m.tokens} />
              ))}
            </Section>

            {/* Skills */}
            <Section
              open={!!open["skills"]}
              onToggle={() => toggle("skills")}
              title={text("Skills", "技能")}
              count={data?.skills_detail?.length || 0}
            >
              {[...(data?.skills_detail || [])]
                .sort((a, b) => b.tokens - a.tokens)
                .map((s) => (
                  <Row key={s.name} name={s.name} tokens={s.tokens} />
                ))}
            </Section>
          </>
        )}
      </div>
    </div>
  );
}
