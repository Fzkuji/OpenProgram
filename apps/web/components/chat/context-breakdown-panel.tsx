"use client";

/**
 * /context 面板 —— 当前会话的 input token 分类分解，对齐 Claude Code /context：
 * 总览（System prompt / System tools loaded+deferred / MCP / Memory / Skills /
 * Messages / Free space）+ Skills / Memory / MCP 各自的明细列表。
 *
 * Snapshot is warmed when the session is focused. This panel never shows
 * a loading spinner — it paints cache (or zeros) immediately.
 */
import React, { useEffect, useMemo, useState } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { MENU_SEPARATOR } from "@/components/chat/top-bar/menu-styles";
import { useSessionStore } from "@/lib/session-store";
import {
  readContextBreakdownCache,
  subscribeContextBreakdownCache,
  type ContextBreakdown as Breakdown,
} from "@/lib/state/context-breakdown-cache";

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

/* Claude 用法：所有用量条统一 BLUE（--usage-bar / --usage-track，定义在
   chat.css 的 .context-breakdown-panel 上，light/dark 各一档）；不再按
   分类配色。*/

/** 6px 圆角蓝色用量条 —— 顶部总量条和每行分解条共用。*/
function UsageBar({ pct }: { pct: number }) {
  return (
    <div
      className="h-[6px] w-full overflow-hidden rounded-full"
      style={{ background: "var(--usage-track)" }}
    >
      <div
        className="h-full rounded-full"
        style={{
          width: `${Math.max(0, Math.min(100, pct))}%`,
          background: "var(--usage-bar)",
        }}
      />
    </div>
  );
}

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
  const ringUsed = useSessionStore((s) =>
    sessionId ? s.tokens[sessionId]?.total_used : undefined,
  );
  const ringBasis = useSessionStore((s) =>
    sessionId ? s.tokens[sessionId]?.basis : undefined,
  );
  const ringWindow = useSessionStore((s) =>
    sessionId ? s.contextWindow[sessionId] : undefined,
  );
  const [data, setData] = useState<Breakdown | null>(() =>
    readContextBreakdownCache(sessionId, headId),
  );
  const [open, setOpen] = useState<Record<string, boolean>>({});

  useEffect(() => {
    const sync = () => {
      const cached = readContextBreakdownCache(sessionId, headId);
      if (cached) {
        setData(cached);
        return;
      }
      if (ringUsed != null || ringWindow) {
        setData({
          total_used: ringUsed,
          window: ringWindow,
          context_window: ringWindow,
          basis: ringBasis ?? undefined,
        });
      }
    };
    sync();
    return subscribeContextBreakdownCache(sync);
  }, [sessionId, headId, ringUsed, ringWindow, ringBasis]);

  // 窗口和总占用都用服务端 stats 里的那一份（圆环读的同一个字段）。
  // 分项仍是本次现场算出的 breakdown —— 明细可以是估算，但顶部的总数
  // 必须和圆环同源。
  const win = data?.window || data?.context_window || 0;
  const pct = (v: number) => (win > 0 ? (v / win) * 100 : 0);

  const totalUsed = data?.total_used ?? win - (data?.free_space ?? 0);
  const usedPct = win > 0 ? Math.min(1, totalUsed / win) : 0;

  const rows = useMemo(() => {
    // 无数据（新会话还没 id）时不出空态文案，照常渲染全 0 面板。
    const d = data ?? ({} as Breakdown);
    const defs: [string, number][] = [
      [text("System prompt", "系统提示"), d.system_prompt || 0],
      [text("System tools", "工具"), d.tools_schema || 0],
      [text("System tools (deferred)", "工具(延迟)"), d.tools_deferred_catalog || 0],
      [text("MCP tools", "MCP 工具"), d.mcp_tools || 0],
      [text("MCP tools (deferred)", "MCP 工具(延迟)"), d.mcp_tools_deferred || 0],
      [text("Memory files", "记忆文件"), d.memory || 0],
      [text("Skills", "技能"), d.skills || 0],
      [text("Messages", "对话消息"), d.messages || 0],
      [text("Other context (estimated)", "其他上下文（估算）"), d.unclassified || 0],
      // 空闲 = 窗口 − 顶部那个总占用，跟着 total_used 走而不是跟着分项
      // 加总走 —— 否则实测总数和估算分项对不上时，头行和这一行互相打架。
      [text("Free space", "空闲"), Math.max(0, win - totalUsed)],
    ];
    // 全部分类都显示（含 0），不过滤 —— 让用户看到每一档存在与否。
    const freeLabel = text("Free space", "空闲");
    return defs.map(([label, v]) => ({
      label,
      tokens: v,
      pct: pct(v),
      zero: v <= 0,
      free: label === freeLabel,
    }));
  }, [data, text, win, totalUsed]);

  const toggle = (k: string) => setOpen((o) => ({ ...o, [k]: !o[k] }));

  return (
    <div
      className="context-breakdown-panel flex max-h-[70vh] w-[380px] flex-col overflow-hidden"
      style={{
        // Grammar C 卡片：白卡 + 10px 圆角（=输入框刻度）+ 真 border
        // + 投影——弹层统一配方。
        background: "var(--surface-popover)",
        borderRadius: 10,
        border: "1px solid var(--border-popover)",
        boxShadow: "var(--shadow-popover)",
      }}
    >
      <div className="flex-1 overflow-y-auto" style={{ padding: "16px 16px 16px" }}>
        {data?.error ? (
          <div className="p-4 text-[12px]" style={{ color: "var(--accent-red)" }}>
            {data.error}
          </div>
        ) : (
          <>
            {/* 标题行 —— Claude grammar C：muted 维度名 + 右对齐 BRIGHT 总量。*/}
            <div className="mb-[10px] flex items-baseline justify-between">
              <span className="text-[13px]" style={{ color: "var(--text-muted)" }}>
                {text("Context window", "上下文窗口")}
                {/* 和圆环 tooltip 同一个标记：估算值标 est.，实测不标。*/}
                {data?.basis === "estimated" && (
                  <span className="ml-1 text-[11px]">{text("(est.)", "（估算）")}</span>
                )}
              </span>
              <span
                className="text-[13px] font-medium"
                style={{ color: "var(--text-bright)" }}
              >
                {fmt(totalUsed)} / {fmt(win)} ({(usedPct * 100).toFixed(1)}%)
              </span>
            </div>
            <UsageBar pct={usedPct * 100} />

            <div className={MENU_SEPARATOR} style={{ marginTop: 14, marginBottom: 14 }} />

            {/* 分类分解 —— 每行：标签左 / muted 数值右 / 下方细蓝条
                （Claude 用量面板 5-hour / weekly 行的形制）。*/}
            <div className="space-y-[10px]">
              {rows.map((r) => (
                <div key={r.label} style={{ opacity: r.zero ? 0.4 : 1 }}>
                  <div className="mb-[4px] flex items-center justify-between text-[12px]">
                    <span style={{ color: "var(--text-primary)" }}>{r.label}</span>
                    <span style={{ color: "var(--text-muted)" }}>
                      {fmt(r.tokens)} · {r.pct.toFixed(1)}%
                    </span>
                  </div>
                  {/* Free space 不是消耗——蓝条语义是"用掉多少"，空闲行
                      画满条会反向误导，只留空轨。 */}
                  <UsageBar pct={r.free ? 0 : r.pct} />
                </div>
              ))}
            </div>

            <div className={MENU_SEPARATOR} style={{ marginTop: 14, marginBottom: 8 }} />

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
