"use client";

/**
 * /context 面板：当前会话的输入 token 构成，两层。
 *
 * 总览：总占用、一根分段条、四类（系统提示 / 工具 / 记忆 / 对话）。
 * 点一类（或分段条上那一段）进入它的详情：这一类自己的分段条和子项，
 * 子项展开看实际发出去的原文；对话详情里的消息点击后跳到聊天中那一条。
 *
 * Snapshot is warmed when the session is focused. This panel never shows
 * a loading spinner — it paints cache (or zeros) immediately.
 */
import React, { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ChevronDown, ChevronRight, FoldVertical } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { FillIcon } from "@/components/ui/fill-icon";
import { useSessionStore } from "@/lib/session-store";
import { cn } from "@/lib/utils";
import {
  CHECK_SLOT_PAD,
  GROUP_LABEL,
  MENU_NOTE,
  MENU_PANEL,
  MENU_SEPARATOR,
  MENU_TRAILING,
  itemCls,
} from "@/components/ui/menu/menu-styles";
import { getSocket } from "@/lib/runtime-bridge/state";
import { loadSessionHistoryWindow } from "@/lib/runtime-bridge/session/session-history-loader";
import { occupancyOf, occupancySnapshot } from "@/lib/chat/context-occupancy";
import {
  readContextBreakdownCache,
  readDraftContextBreakdown,
  subscribeContextBreakdownCache,
  type ContextBreakdown as Breakdown,
} from "@/lib/chat/context-breakdown-cache";
import { isCompacting } from "@/lib/chat/run/run-operation";
import { formatMessageTime } from "@/lib/chat/transcript/message-time";
import { announceReaderSeek } from "@/lib/chat/history/chat-scroll";

interface Props {
  sessionId: string | null;
  /** 当前分支头（DAG 选中的分支）。传入则按该分支算上下文；切分支时变化
   *  会触发重新拉取。缺省时后端回退会话全局 head。*/
  headId?: string | null;
  /** Closes the panel; a message row uses it before jumping to the message. */
  onClose?: () => void;
}

type Text = (en: string, zh: string) => string;

function fmt(n: number): string {
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return String(Math.round(n));
}

function pct(part: number, whole: number): string {
  return whole > 0 ? `${((part / whole) * 100).toFixed(1)}%` : "0%";
}

/** One line of a detail view; ``text`` makes it expandable, ``onOpen``
 *  makes it a link. */
interface Item {
  key: string;
  label: string;
  tokens: number;
  hint?: string;
  text?: string;
  onOpen?: () => void;
}

interface Group {
  key: string;
  title?: string;
  /** The rows overlap other groups, so their sum isn't shown. */
  noTotal?: boolean;
  items: Item[];
}

interface Category {
  key: "system" | "tools" | "memory" | "messages" | "unclassified";
  label: string;
  tokens: number;
  color: string;
  groups: Group[];
}

/** Names of the system prompt's components (context/prompt_assembly/components.py). A
 *  component a plugin registers shows under its own name. */
function systemSectionLabel(name: string, text: Text): string {
  switch (name) {
    case "base": return text("Base prompt", "基础提示词");
    case "output_style": return text("Output style", "输出风格");
    case "inline_prompt": return text("Agent system prompt", "Agent 系统提示");
    case "environment": return text("Environment", "环境");
    case "agents_md": return "AGENTS.md";
    case "capabilities": return text("Programs and tool notes", "Program 与工具说明");
    case "skills": return text("Skills index", "技能索引");
    case "current_date": return text("Date", "日期");
    case "plan_mode": return text("Plan mode", "计划模式");
    case "other": return text("Other", "其他");
    default: return name;
  }
}

function messageKindLabel(kind: string, text: Text): string {
  switch (kind) {
    case "user": return text("Your messages", "你的消息");
    case "assistant": return text("Replies, thinking and tool calls", "回复、思考与工具调用");
    case "summary": return text("Compaction summary", "压缩摘要");
    case "turn": return text("This turn so far", "本轮进行中");
    default: return text("Other", "其他");
  }
}

/** The last compaction on the branch, as one sentence under Compact. */
function lastCompactionNote(c: NonNullable<Breakdown["last_compaction"]>, text: Text): string {
  const when = typeof c.at === "number" && c.at > 0 ? formatMessageTime(new Date(c.at * 1000)) : "";
  const sizes = c.tokens_before != null && c.tokens_after != null
    ? `, ${fmt(c.tokens_before)} → ${fmt(c.tokens_after)}` : "";
  return text(
    `Last compacted${when ? ` ${when}` : ""}: ${c.covered} messages${sizes}`,
    `上次压缩${when ? ` ${when}` : ""}：${c.covered} 条消息${sizes}`,
  );
}

/** A message preview without Markdown punctuation. */
function plain(textValue: string): string {
  return textValue.replace(/[*_`#>]+/g, "").replace(/\s+/g, " ").trim();
}

/** Scroll the transcript to a message and flash it, loading the page of
 *  history around it first when it isn't in the transcript yet (the same
 *  route find-in-conversation takes). */
async function revealMessage(sessionId: string, id: string): Promise<void> {
  const loaded = useSessionStore.getState().messageOrder[sessionId]?.includes(id);
  if (!loaded) await loadSessionHistoryWindow(sessionId, "around", id);
  const esc = CSS.escape(id);
  const find = () => document.querySelector<HTMLElement>(
    `#chatMessages [data-msg-id="${esc}"], #chatMessages [data-msg-ids~="${esc}"]`,
  );
  // A recycled row is a sized slot until it is live; bring it near first.
  for (let i = 0; i < 20 && !find(); i += 1) {
    const slot = document.querySelector<HTMLElement>(`[data-msg-slot="${esc}"]`);
    // A seek started outside the transcript: a growing reply must not pull
    // the view back to the latest while it moves.
    if (slot) announceReaderSeek(slot);
    slot?.scrollIntoView({ block: "center" });
    await new Promise((resolve) => requestAnimationFrame(resolve));
  }
  const el = find();
  if (!el) return;
  announceReaderSeek(el);
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add("dag-flash");
  window.setTimeout(() => el.classList.remove("dag-flash"), 1400);
}

function buildCategories(d: Breakdown, text: Text, onReveal: (id: string) => void): Category[] {
  const systemItems: Item[] = [];
  for (const row of d.system_detail || []) {
    systemItems.push({
      key: row.name, label: systemSectionLabel(row.name, text),
      tokens: row.tokens, text: row.text,
    });
    if (row.name === "capabilities" && (d.skills || 0) > 0) {
      systemItems.push(skillsItem(d, text));
    }
  }
  if ((d.skills || 0) > 0 && !systemItems.some((item) => item.key === "skills")) {
    systemItems.push(skillsItem(d, text));
  }

  const mcpNames = new Set((d.mcp_detail || []).map((row) => row.name));
  const builtin = (d.tools || []).filter((row) => !mcpNames.has(row.name));
  const toolItem = (row: { name: string; tokens: number; description?: string }): Item => ({
    key: row.name, label: row.name, tokens: row.tokens, text: row.description,
  });
  const byTokens = (a: Item, b: Item) => b.tokens - a.tokens;
  const toolGroups: Group[] = [
    { key: "loaded", title: text("Loaded", "已加载"),
      items: builtin.filter((r) => !r.deferred).map(toolItem).sort(byTokens) },
    { key: "deferred", title: text("Loaded on demand (names only)", "按需加载（只发名字）"),
      items: builtin.filter((r) => r.deferred).map(toolItem).sort(byTokens) },
    { key: "mcp", title: "MCP",
      items: (d.mcp_detail || []).map((row) => ({
        ...toolItem(row),
        hint: [row.server, row.deferred ? text("on demand", "按需") : ""]
          .filter(Boolean).join(" · "),
      })).sort(byTokens) },
  ];

  const messages = d.messages_detail;
  const messageGroups: Group[] = [
    { key: "kinds", items: (messages?.groups || []).map((g) => ({
      key: g.kind, label: messageKindLabel(g.kind, text), tokens: g.tokens,
      hint: g.count > 0 ? text(`${g.count} messages`, `${g.count} 条`) : undefined,
    })) },
    { key: "top", title: text("Largest messages", "最大的消息"), noTotal: true,
      items: (messages?.top || []).map((m) => ({
        key: m.id, label: plain(m.preview) || m.id, tokens: m.tokens,
        hint: m.kind === "user" ? text("You", "你")
          : m.kind === "summary" ? text("Summary", "摘要") : text("Reply", "回复"),
        onOpen: () => onReveal(m.id),
      })) },
  ];

  return [
    { key: "system", label: text("System prompt", "系统提示"),
      tokens: (d.system_prompt || 0) + (d.skills || 0),
      color: "var(--context-system-prompt)", groups: [{ key: "all", items: systemItems }] },
    { key: "tools", label: text("Tools", "工具"),
      tokens: (d.tools_schema || 0) + (d.tools_deferred_catalog || 0)
        + (d.mcp_tools || 0) + (d.mcp_tools_deferred || 0),
      color: "var(--context-system-tools)", groups: toolGroups },
    { key: "memory", label: text("Memory", "记忆"), tokens: d.memory || 0,
      color: "var(--context-memory)",
      groups: [{ key: "files", items: (d.memory_detail || []).map((row) => ({
        key: row.path, label: row.path, tokens: row.tokens, text: row.text,
      })) }] },
    { key: "messages", label: text("Conversation", "对话"), tokens: d.messages || 0,
      color: "var(--context-messages)", groups: messageGroups },
    { key: "unclassified", label: text("Unclassified", "未归类"),
      tokens: d.unclassified || 0, color: "var(--context-other)", groups: [] },
  ];
}

function skillsItem(d: Breakdown, text: Text): Item {
  const names = (d.skills_detail || []).map((s) => s.name);
  return {
    key: "skills", label: systemSectionLabel("skills", text), tokens: d.skills || 0,
    hint: names.length
      ? text(names.length === 1 ? "1 skill" : `${names.length} skills`, `${names.length} 个`)
      : undefined,
    text: names.length ? names.join("\n") : undefined,
  };
}

/** Stacked meter; ``onPick`` makes each segment a button. */
function StackedBar({
  segments,
  total,
  hovered,
  onHover,
  onPick,
}: {
  segments: { key: string; label: string; tokens: number; color: string }[];
  total: number;
  hovered: string | null;
  onHover: (key: string | null) => void;
  onPick?: (key: string) => void;
}) {
  let offset = 0;
  return (
    <div className="relative h-[12px] w-full" onPointerLeave={() => onHover(null)}>
      <span
        className="absolute inset-x-0 top-[3px] h-[6px] rounded-full"
        style={{ background: "var(--usage-track)" }}
      />
      {segments.map((segment) => {
        if (segment.tokens <= 0 || total <= 0) return null;
        const left = (offset / total) * 100;
        offset += segment.tokens;
        const active = hovered === segment.key;
        return (
          <span
            key={segment.key}
            role={onPick ? "button" : undefined}
            aria-label={`${segment.label}: ${fmt(segment.tokens)}`}
            className="absolute transition-[top,height] duration-150"
            style={{
              left: `${left}%`,
              width: `${(segment.tokens / total) * 100}%`,
              minWidth: 2,
              top: active ? 0 : 3,
              height: active ? 12 : 6,
              background: segment.color,
              zIndex: active ? 2 : 1,
              // A hairline in the card colour keeps neighbouring shades apart.
              boxShadow: "inset -1px 0 0 var(--surface-popover)",
              cursor: onPick ? "pointer" : undefined,
            }}
            onPointerEnter={() => onHover(segment.key)}
            onClick={onPick ? () => onPick(segment.key) : undefined}
          />
        );
      })}
    </div>
  );
}

/** A category colour in the row's icon column, so labels line up with
 *  every other menu's icon + text rows. */
function Swatch({ color }: { color?: string }) {
  return (
    <span className="flex size-4 shrink-0 items-center justify-center">
      {color && <span className="size-2 rounded-2xs" style={{ background: color }} />}
    </span>
  );
}

function shade(color: string, index: number): string {
  const strength = Math.max(35, 100 - index * 16);
  return `color-mix(in srgb, ${color} ${strength}%, var(--usage-track))`;
}

/** Trailing token count, then the row's chevron (or its empty slot). */
function Trailing({ value, chevron }: { value: string; chevron?: "right" | "down" | null }) {
  return (
    <span className={MENU_TRAILING}>
      <span className="text-fs-2xs text-text-muted">{value}</span>
      {chevron === "right" ? <ChevronRight className="size-4 text-text-muted" />
        : chevron === "down" ? <ChevronDown className="size-4 text-text-muted" />
        : <span className={CHECK_SLOT_PAD} />}
    </span>
  );
}

function ItemRow({
  item,
  color,
  open,
  onToggle,
  onHover,
}: {
  item: Item;
  color?: string;
  open: boolean;
  onToggle: () => void;
  onHover: (on: boolean) => void;
}) {
  const expandable = Boolean(item.text);
  return (
    <>
      <button
        type="button"
        onClick={item.onOpen ?? (expandable ? onToggle : undefined)}
        onPointerEnter={() => onHover(true)}
        onPointerLeave={() => onHover(false)}
        className={cn(itemCls(open), "w-full text-left",
          !expandable && !item.onOpen && "cursor-default")}
      >
        <Swatch color={color} />
        <span className="min-w-0 flex-1 truncate">{item.label}</span>
        {item.hint && <span className="shrink-0 text-fs-2xs text-text-muted">{item.hint}</span>}
        <Trailing
          value={fmt(item.tokens)}
          chevron={expandable ? (open ? "down" : "right") : null}
        />
      </button>
      {expandable && open && (
        // Starts under the label (row padding + icon column + gap).
        <pre className="max-h-42 shrink-0 overflow-y-auto whitespace-pre-wrap break-words py-1 pr-1.5 pl-6 font-mono text-fs-2xs text-text-muted">
          {item.text}
        </pre>
      )}
    </>
  );
}

export function ContextBreakdownPanel({ sessionId, headId, onClose }: Props) {
  const { text } = useTranslation();
  const ringStats = useSessionStore((s) =>
    sessionId ? s.tokens[sessionId] : undefined,
  );
  const lastMeasured = useSessionStore((s) =>
    sessionId ? s.tokens[sessionId]?.context : undefined,
  );
  const ringWindow = useSessionStore((s) =>
    sessionId ? s.contextWindow[sessionId] : undefined,
  );
  const compacting = useSessionStore((s) =>
    Boolean(sessionId && isCompacting(s.runningTasks[sessionId])),
  );
  const canCompact = Boolean(sessionId) && !compacting;
  const draftProvider = useSessionStore((s) => s.agentSettings.chat?.provider);
  const draftModel = useSessionStore((s) => s.agentSettings.chat?.model);
  const draftProjectId = useSessionStore((s) =>
    s.activeChatKey ? s.pendingProjectsByChat[s.activeChatKey] ?? null : null);
  // A chat with no session yet reads the draft estimate (the badge warms it).
  const readCached = () => sessionId
    ? readContextBreakdownCache(sessionId, headId)
    : readDraftContextBreakdown(draftProvider, draftModel, draftProjectId);
  const [data, setData] = useState<Breakdown | null>(readCached);
  const [view, setView] = useState<Category["key"] | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [hovered, setHovered] = useState<string | null>(null);

  useEffect(() => {
    const sync = () => {
      // The ring reads this same snapshot (lib/chat/context-occupancy).
      const next = sessionId ? occupancySnapshot(readCached(), ringStats, ringWindow) : readCached();
      if (next) setData(next);
    };
    sync();
    return subscribeContextBreakdownCache(sync);
    // readCached reads only these.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, headId, ringStats, ringWindow, draftProvider, draftModel, draftProjectId]);

  // 顶部的总数和窗口与圆环同源（occupancyOf 读同一份快照），分项仍是
  // 这份 breakdown —— 明细可以是估算。窗口未知（模型解析不到）时为 0：
  // 只显示用量，不显示上限和百分比。
  const { used: totalUsed, window: win } = occupancyOf(data);
  const usedPct = win > 0 ? Math.min(1, totalUsed / win) : 0;

  const categories = useMemo(
    () => buildCategories(data ?? ({} as Breakdown), text, (id) => {
      onClose?.();
      if (sessionId) void revealMessage(sessionId, id);
    }),
    [data, text, onClose, sessionId],
  );
  const shown = categories.filter((c) => c.tokens > 0 || c.key !== "unclassified");
  const current = view ? categories.find((c) => c.key === view) ?? null : null;

  const openView = (key: string) => {
    const target = categories.find((c) => c.key === key);
    if (!target || target.key === "unclassified") return;
    setView(target.key);
    setHovered(null);
    setExpanded({});
  };

  const compact = () => {
    if (!sessionId || !canCompact) return;
    const sock = getSocket();
    if (sock && sock.readyState === WebSocket.OPEN) {
      sock.send(JSON.stringify({ action: "compact", session_id: sessionId }));
    }
  };
  const compactRow = (
    <button
      type="button"
      aria-disabled={!canCompact}
      aria-busy={compacting}
      onClick={compact}
      className={cn(itemCls(false), "w-full text-left text-text-muted",
        !canCompact && "cursor-default opacity-50 hover:bg-transparent")}
    >
      <FillIcon><FoldVertical /></FillIcon>
      <span className="flex-1">
        {compacting ? text("Compacting…", "正在压缩…") : text("Compact conversation", "压缩对话")}
      </span>
    </button>
  );

  const detailBar = (category: Category) => {
    const segments = category.key === "tools"
      ? category.groups.map((g, i) => ({
        key: g.key, label: g.title || g.key,
        tokens: g.items.reduce((sum, item) => sum + item.tokens, 0),
        color: shade(category.color, i * 2),
      }))
      : (category.groups[0]?.items ?? []).map((item, i) => ({
        key: item.key, label: item.label, tokens: item.tokens,
        color: shade(category.color, i),
      }));
    const total = segments.reduce((sum, seg) => sum + seg.tokens, 0);
    if (total <= 0 || category.key === "messages") return null;
    return (
      <div className="px-1.5 py-1">
        <StackedBar segments={segments} total={total} hovered={hovered} onHover={setHovered} />
      </div>
    );
  };

  return (
    <div className={cn(MENU_PANEL, "context-breakdown-panel w-[320px]")}>
      {data?.error ? (
        <div className="px-1.5 py-1 text-fs-xs text-accent-red">{data.error}</div>
      ) : current ? (
        <>
          <button
            type="button"
            aria-label={text("Back", "返回")}
            onClick={() => { setView(null); setHovered(null); }}
            className={cn(itemCls(false), "w-full text-left")}
          >
            <FillIcon><ArrowLeft /></FillIcon>
            <span className="min-w-0 flex-1 truncate text-text-bright">{current.label}</span>
            <Trailing value={`${fmt(current.tokens)} · ${pct(current.tokens, totalUsed)}`} />
          </button>
          {detailBar(current)}
          {current.groups.every((g) => g.items.length === 0) && (
            <div className="px-1.5 py-1 text-fs-xs text-text-muted">
              {text("Nothing recorded for this part yet.", "这一部分还没有记录。")}
            </div>
          )}
          {current.groups.map((group, gi) => group.items.length > 0 && (
            <React.Fragment key={group.key}>
              {(gi > 0 || group.title) && <div className={MENU_SEPARATOR} />}
              {group.title && (
                <div className={GROUP_LABEL}>
                  <span className="flex-1">{group.title}</span>
                  {!group.noTotal && (
                    <span>
                      {fmt(group.items.reduce((sum, item) => sum + item.tokens, 0))}
                    </span>
                  )}
                </div>
              )}
              {group.items.map((item, i) => {
                const id = `${group.key}/${item.key}`;
                return (
                  <ItemRow
                    key={id}
                    item={item}
                    color={current.key === "tools" || group.key === "top"
                      ? undefined
                      : shade(current.color, i)}
                    open={Boolean(expanded[id])}
                    onToggle={() => setExpanded((e) => ({ ...e, [id]: !e[id] }))}
                    onHover={(on) => setHovered(on ? item.key : null)}
                  />
                );
              })}
            </React.Fragment>
          ))}
        </>
      ) : (
        <>
          <div className={GROUP_LABEL}>
            <span className="flex-1">{text("Context window", "上下文窗口")}</span>
            {/* 和圆环 tooltip 同一个标记：估算值标 est.，实测不标。*/}
            {data?.basis === "estimated" && (
              <span>
                {text("est.", "估算")}
                {lastMeasured ? ` · ${text("last measured", "上次实测")} ${fmt(lastMeasured)}` : ""}
              </span>
            )}
          </div>
          <div className="flex items-baseline gap-1.5 px-1.5 pt-0.5 text-fs-xs">
            <span className="text-text-bright">
              {win > 0 ? `${fmt(totalUsed)} / ${fmt(win)}` : fmt(totalUsed)}
            </span>
            {win > 0 && (
              <span className="text-fs-2xs text-text-muted">{(usedPct * 100).toFixed(1)}%</span>
            )}
          </div>
          <div className="px-1.5 pt-1.5 pb-1">
            <StackedBar
              segments={[
                ...shown,
                { key: "free", label: text("Free space", "剩余空间"),
                  tokens: Math.max(0, win - totalUsed), color: "transparent" },
              ]}
              total={Math.max(win, totalUsed)}
              hovered={hovered}
              onHover={setHovered}
              onPick={openView}
            />
          </div>
          {shown.map((category) => {
            const clickable = category.key !== "unclassified";
            return (
              <button
                key={category.key}
                type="button"
                onClick={() => openView(category.key)}
                onPointerEnter={() => setHovered(category.key)}
                onPointerLeave={() => setHovered(null)}
                data-tip={clickable ? undefined : text(
                  "The difference between the total input usage and locally classified estimates. It can change after a request or compaction.",
                  "总输入用量与本地分类估算之和的差额。请求完成或压缩后，这个差额可能变化。",
                )}
                className={cn(itemCls(hovered === category.key), "w-full text-left",
                  !clickable && "cursor-default")}
              >
                <Swatch color={category.color} />
                <span className="min-w-0 flex-1 truncate">{category.label}</span>
                <Trailing
                  value={`${fmt(category.tokens)} · ${pct(category.tokens, totalUsed)}`}
                  chevron={clickable ? "right" : null}
                />
              </button>
            );
          })}
          {/* A chat with no session has nothing to compact. */}
          {sessionId && <div className={MENU_SEPARATOR} />}
          {sessionId && compactRow}
          {sessionId && data?.last_compaction && (
            <div className={cn(MENU_NOTE, "pt-0.5")} data-last-compaction>
              {lastCompactionNote(data.last_compaction, text)}
            </div>
          )}
        </>
      )}
    </div>
  );
}
