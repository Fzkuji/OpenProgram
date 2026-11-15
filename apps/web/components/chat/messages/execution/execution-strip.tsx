"use client";

/**
 * 执行时间线 — docs/reference/design/ui/chat/chat-turn-visual-spec.html 的实现。
 *
 * 分工：聊天里只画**调用树结构**（行 + 层级），详情看右栏——点击函数
 * / 子代理行调 `showDetail`，右栏 Executions 面板显示输入/输出/耗时
 * （复用共享详情面板）。行尾 ⌄N 只展开子行
 * （递归层级），不在聊天里倒 JSON。思考行例外：内容轻，点行内联展开；
 * 子代理行点行内联展开它自己的对话。
 *
 * 行图标统一用 Solar Bold Duotone（tool-presentation.ts 按工具选图，
 * 思考 / LLM / 子代理 / 通用函数各有兜底），静态不动画。
 * 图标绝对定位在标题行内部，与文字共用同一垂直中心。流式进行中的一轮
 * 不走这里（assistant-bubble 平铺实时块），落定后切到本组件。
 */
import { stepOpenByDefault } from "@/lib/prefs/transcript-view";
import { useTranscriptViewHere } from "../list/transcript-view-scope";
import { sendExecutionCancel } from "@/lib/execution/cancel-execution";
import { memo, useEffect, useState } from "react";
import { ArrowUpRight, Check, ChevronRight, Copy, Square } from "lucide-react";
import { SolarIcon, type SolarIconName } from "@/components/solar-icons";
import { presentProviderSearch, presentTool, summarizeResult, type ToolTone } from "./tool-presentation";
import { parseSearchSources, searchQuery } from "@/lib/chat/transcript/web-sources";
import { SearchSourceList } from "../bubbles/web-sources";
import { guiResultNote, isGuiToolCall } from "./gui-tool-view";
import { summarizeToolGroup, type ToolGroupSummary } from "./tool-group-summary";
import { useChosenOpen } from "./step-expansion";
import { spawnFacts } from "@/lib/chat/transcript/owned-children";
import { isAgentRunning, isStopped } from "@/lib/chat/transcript/row-status";
import { afterTwoAnimationFrames } from "../list/collapse-frame";

import type { AssistantBlock, ChatMsg, DetailNode } from "@/lib/session-store";
import { useSessionStore } from "@/lib/session-store";
import type { TNode } from "./tree-types";
import { useTranslation } from "@/lib/i18n";
import {
  useExecutionStreamStore,
  streamPreviewForNode,
} from "@/lib/execution-stream";
import { fetchFullToolOutput } from "@/lib/net/tool-output";
import { copyText as copyToClipboard } from "@/lib/clipboard";
import { showToast } from "@/lib/format-utils/toast";
import { sendRuntimeCommand } from "@/lib/runtime-bridge/session/session-load";
import { checkoutBranch } from "@/lib/runtime-bridge/session/conversations";
import { loadSubAgentTranscript } from "@/lib/chat/transcript/sub-agent-transcripts";
import { SubAgentTranscript, subAgentKey } from "../cards/sub-agent-transcript";
import { LlmNodeContent } from "./llm-node-content";
import { renderMarkdown, useMarkdownReady } from "../bubbles/markdown";
import { MessageTimestamp } from "../bubbles/message-actions";
import { CodeRainIcon, CodeRainText, useCodeRainStyle } from "../../welcome/code-rain-style";

export { SPAWNING_TOOL_NAMES } from "./tool-group-summary";

/** Commands go out as they are; a `load_session` goes through the managed
 *  session read, since the transcript applies only the reply to a read it
 *  registered. */
function wsSend(payload: unknown): void {
  sendRuntimeCommand(payload);
}

/** 高度过渡容器：grid 0fr↔1fr 动画，展开向下推、收起平滑抽走。
 *  关闭动画结束后卸载子树，折叠状态不付渲染成本。 */
function Collapse({ open, restored, children }: {
  open: boolean;
  /** Opened earlier and mounting again: it appears open, with no animation. */
  restored?: boolean;
  children: React.ReactNode;
}) {
  const [shown, setShown] = useState(!!restored && open);
  const [mounted, setMounted] = useState(open);
  useEffect(() => {
    if (open) {
      setMounted(true);
      return afterTwoAnimationFrames(() => setShown(true));
    }
    setShown(false);
    const t = setTimeout(() => setMounted(false), 220);
    return () => clearTimeout(t);
  }, [open]);
  if (!mounted) return null;
  return (
    <div className={"tl-collapse" + (shown ? " is-open" : "")}>
      <div className="tl-collapse-inner">{children}</div>
    </div>
  );
}

/** 时间线外壳：一行淡文字摘要 ›，点击展开竖线时间线。
 *
 *  是否默认展开取决于对话的显示方式（lib/prefs/transcript-view.ts）：普通
 *  视图收起，思考和详细视图展开。流式摘要仍以 shimmer 表示进行中；用户手动
 *  展开或收起后，本轮后续步骤、终态更新和显示方式都不覆盖该选择。
 *
 *  `label` is either a plain string or a tool-group summary
 *  (tool-group-summary.ts), which adds the red failure note and the
 *  +N −M badge. The label truncates to one line; the hover tip shows it whole. */
export function ExecutionStrip({
  label,
  streaming,
  children,
  after,
  subagentHeads,
  stopped,
  status,
  expansionId,
}: {
  /** Keeps the reader's open or closed choice across rebuilds of this row. */
  expansionId?: string;
  label: string | ToolGroupSummary;
  /** A live state after the sentence, such as a retry (retry-status.tsx). */
  status?: React.ReactNode;
  /** The turn ended here because the user stopped it (or it was cut off):
   *  the row says so with a tag, as Claude and Codex mark a stopped step. */
  stopped?: "cancelled" | "interrupted";
  streaming?: boolean;
  children: React.ReactNode;
  /** Content that collapses with the trace but stays outside its vertical line. */
  after?: React.ReactNode;
  /** Branch heads owned by this strip, used to reopen the exact row on return. */
  subagentHeads?: Array<string | undefined>;
}) {
  // The reader's own click wins over the conversation's view.
  const view = useTranscriptViewHere();
  const codeRain = useCodeRainStyle();
  const [chosen, setOpen] = useChosenOpen(expansionId);
  const open = chosen ?? view !== "normal";
  const { text } = useTranslation();
  const summary: ToolGroupSummary = typeof label === "string"
    ? { label, failed: 0, added: null, removed: null }
    : label;
  const failedNote = summary.failed > 0
    ? text(`${summary.failed} failed`, `${summary.failed} 个失败`)
    : "";
  const hasBadge = summary.added !== null && summary.removed !== null;
  return (
    <div
      className="tl"
      data-open={open ? "1" : "0"}
      data-subagent-heads={subagentHeads?.filter(Boolean).join(" ") || undefined}
    >
      {/* No hover tip: the row is transcript content, not a control whose
          purpose needs explaining. */}
      <button
        type="button"
        className="tl-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {!summary.label ? null : streaming && codeRain
          ? <CodeRainText className="tl-label" text={summary.label} />
          : <span className={"tl-label" + (streaming ? " tl-label-shimmer" : "")}>{summary.label}</span>}
        {status}
        {/* The dot is its own item, so the row's gap spaces both its sides. */}
        {failedNote ? <><span className="tl-fail" aria-hidden="true">·</span><span className="tl-fail">{failedNote}</span></> : null}
        {stopped ? (
          <span className="tl-stopped">{stopped === "cancelled"
            ? text("Stopped", "已停止") : text("Interrupted", "已中断")}</span>
        ) : null}
        {hasBadge ? (
          <span className="turn-files-diff tl-diff">
            <span className="turn-files-stat is-add"><span>+</span>{summary.added}</span>
            <span className="turn-files-stat is-del"><span>−</span>{summary.removed}</span>
          </span>
        ) : null}
        <ChevronRight className="tl-chev" size={14} aria-hidden="true" />
      </button>
      <Collapse open={open} restored={chosen === true}>
        <div className="tl-body">{children}</div>
        {after}
      </Collapse>
    </div>
  );
}

/** 后端 ensure_ascii 序列化出的 \\uXXXX 转义还原成真实字符。 */
export function decodeEscapes(s: string): string {
  if (!s.includes("\\u")) return s;
  return s.replace(/\\u([0-9a-fA-F]{4})/g,
    (_, h) => String.fromCharCode(parseInt(h, 16)));
}

/** The execution tree is shared by reference and can be large; compare it
 *  by identity and serialize only the remaining fields. */
function sameDetail(a: DetailNode, b: DetailNode): boolean {
  if (a.tree_root !== b.tree_root) return false;
  return JSON.stringify({ ...a, tree_root: undefined }) === JSON.stringify({ ...b, tree_root: undefined });
}

function short(v: unknown, n = 90): string {
  let s: string;
  if (typeof v === "string") s = decodeEscapes(v);
  else {
    try { s = JSON.stringify(v); } catch { s = String(v); }
  }
  s = (s || "").replace(/\s+/g, " ").trim();
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function firstLine(s: string): string {
  const t = (s || "").trim();
  const nl = t.indexOf("\n");
  return nl > 0 ? t.slice(0, nl) : t;
}

function lastLine(s: string): string {
  const lines = (s || "").trim().split("\n").filter((l) => l.trim());
  return lines[lines.length - 1] || "";
}

/** note 是纯文本一行，markdown 标记（**、`）原样露出来很难看——摘掉。 */
function plainNote(s: string): string {
  return s.replace(/\*\*|__|`/g, "");
}

/** 单个步骤行。
 *
 *  点击语义（用户裁决 2026-07-11）：
 *  - **标题文字 = 超链接**：点标题 → 右栏 Executions 显示详情。
 *  - **行空白 / 图标 = 展开开关**：有子树切子树（默认折叠），思考行切
 *    内联全文；两者都没有的叶子行不响应。
 *  图标静态不动画；三类配色区分（思考紫 / 函数橙 / LLM 青 / 子代理绿）；
 *  可展开行在原图标外增加常驻细圆圈；每一行都有复制动作。 */
export function StepRow({
  icon,
  glyph,
  tone,
  title,
  target,
  note,
  error,
  running,
  meta,
  actions,
  copyText,
  detail,
  onOpenDetail,
  syncDetail,
  inlineBody,
  subSteps,
  defaultKidsOpen,
  stateKey,
  open: openProp,
  onOpenChange,
  dataMsgId,
  dataHeadId,
  expansionId,
}: {
  /** Keeps the reader's open or closed choice across rebuilds of this row. */
  expansionId?: string;
  icon: "thinking" | "function" | "llm" | "subagent";
  /** Per-tool icon; overrides the generic one for `icon`. */
  glyph?: SolarIconName;
  /** Per-tool colour family (tool-presentation.ts). */
  tone?: ToolTone;
  title: string;
  /** Identifying argument, shown as a monospace chip after the title. */
  target?: string;
  note?: string;
  error?: boolean;
  running?: boolean;
  /** Muted text before the buttons (a timestamp). */
  meta?: React.ReactNode;
  /** Buttons after Copy. */
  actions?: React.ReactNode;
  copyText?: string;
  detail?: DetailNode;
  onOpenDetail?: () => void;
  /** Custom detail loaders opt in after their in-flight output request settles. */
  syncDetail?: boolean;
  inlineBody?: React.ReactNode;
  subSteps?: React.ReactNode;
  /** 仅当前行的直接子树初始展开。 */
  defaultKidsOpen?: boolean;
  /** Optional persistent key for the inline LLM node expansion state. */
  stateKey?: string;
  /** Inline body open state owned by the caller. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  dataMsgId?: string;
  dataHeadId?: string;
}) {
  const view = useTranscriptViewHere();
  const [chosen, setOpen] = useChosenOpen(expansionId);
  const open = chosen ?? stepOpenByDefault(view, icon);
  const kidsOpen = chosen ?? !!defaultKidsOpen;
  const [copied, setCopied] = useState(false);
  const { text } = useTranslation();
  const codeRain = useCodeRainStyle() && !!running;
  const persistedOpen = useSessionStore((state) =>
    stateKey ? state.llmContentOpen[stateKey] : undefined,
  );
  const setPersistedOpen = useSessionStore((state) => state.setLlmContentOpen);
  const toggleable = !!subSteps || !!inlineBody;
  const expanded = subSteps ? kidsOpen : openProp !== undefined ? openProp : stateKey
    ? (persistedOpen ?? stepOpenByDefault(view, icon))
    : open;
  const copyValue = copyText ?? [title, target, note].filter(Boolean).join(" · ");
  useEffect(() => {
    // Update the selected snapshot without changing the dock or its active tab.
    if (!detail || (onOpenDetail && !syncDetail)) return;
    const store = useSessionStore.getState();
    const current = store.detailNode;
    if (current?.path === detail.path
        && (!current.session_id || !detail.session_id || current.session_id === detail.session_id)
        && !sameDetail(current, detail)) {
      store.populateDetail(detail);
    }
  }, [detail, onOpenDetail, syncDetail]);
  function copy(e: React.MouseEvent) {
    e.stopPropagation();
    void copyToClipboard(copyValue).then((ok) => {
      if (!ok) {
        showToast(text("Could not copy", "未能复制"), { tone: "error" });
        return;
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    });
  }
  function toggle() {
    if (subSteps) setOpen(!kidsOpen);
    else if (inlineBody) {
      const next = !expanded;
      if (onOpenChange) onOpenChange(next);
      else if (stateKey) setPersistedOpen(stateKey, next);
      else setOpen(next);
    }
  }
  function openDetail(e: React.MouseEvent) {
    if (!detail) return;
    e.stopPropagation();
    if (onOpenDetail) onOpenDetail();
    else useSessionStore.getState().showDetail(detail);
  }
  const glyphName: SolarIconName = glyph ?? (icon === "thinking" ? "lightbulb-bolt"
    : icon === "subagent" ? "ghost"
    : icon === "llm" ? "cpu" : "sledgehammer");
  return (
    <div
      className={"tl-step" + (expanded ? " open" : "")}
      data-msg-id={dataMsgId}
      data-head-id={dataHeadId}
    >
      <div
        className="tl-step-head"
        onClick={toggle}
        aria-expanded={toggleable ? expanded : undefined}
        style={toggleable ? undefined : { cursor: "default" }}
      >
        <span
          className={
            "tl-step-icon k-" + icon
            + (tone ? " t-" + tone : "")
            + (error ? " is-error" : "")
            + (running ? " is-running" : "")
            + (toggleable ? " is-toggleable" : "")
          }
          aria-hidden="true"
        >
          {error && !running ? (
            <SolarIcon name="danger-triangle" size={20} motionPreset="none" />
          ) : codeRain ? (
            <CodeRainIcon size={20} />
          ) : (
            <SolarIcon name={glyphName} size={20} motionPreset="none" />
          )}
        </span>
        <span
          className={"tl-step-title" + (error ? " is-error" : "")
            + (detail ? " tl-step-link" : "")}
          onClick={detail ? openDetail : undefined}
        >
          {codeRain ? <CodeRainText text={title} /> : title}
        </span>
        {target ? <code className="tl-step-target">{target}</code> : null}
        {/* 不加 title：流式中 note 每个 delta 都在变，原生 tooltip 会
            钉死在视口角落变成"漂浮黑框"；全文本来就有行内展开可看。 */}
        {note ? <span className="tl-step-note">{note}</span> : <span className="tl-step-note" />}
        {toggleable ? <ChevronRight className="tl-step-chev" size={14} aria-hidden="true" /> : null}
        <span className="tl-step-act">
          {meta}
          <button
            type="button"
            className="tl-btn"
            onClick={copy}
            aria-label={copied ? text("Copied", "已复制") : text("Copy", "复制")}
          >
            {copied ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
            <span className="tl-btn-label">{copied ? text("Copied", "已复制") : text("Copy", "复制")}</span>
          </button>
          {actions}
        </span>
      </div>
      {inlineBody ? (
        <Collapse open={expanded} restored={chosen === true}>
          <div className="tl-step-body">{inlineBody}</div>
        </Collapse>
      ) : null}
      {subSteps ? (
        <Collapse open={kidsOpen} restored={chosen === true}>
          <div className="tl-sub">{subSteps}</div>
        </Collapse>
      ) : null}
    </div>
  );
}

/** 思考步骤：内容轻，点行内联展开。
 *  流式中（running）note 显示**最新**一行——最近在想什么；落定后显示
 *  第一行作固定摘要。展开态的全文随 delta 向下拼接生长。 */
const ThinkingBody = memo(function ThinkingBody({ text }: { text: string }) {
  return (
    <div
      className="chat-text"
      dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }}
    />
  );
});

/** Where a worker restart cut the reply off and a new turn carried the work
 *  on (lib/chat/transcript/restart-continuation.ts). */
export function RestartStep() {
  const { text } = useTranslation();
  return (
    <StepRow
      icon="function"
      glyph="restart"
      tone="system"
      title={text("Worker restarted, continued", "Worker 已重启，已继续")}
    />
  );
}

export const ThinkingStep = memo(function ThinkingStep({ text: thinkingText, running, expansionId }: {
  text: string;
  running?: boolean;
  expansionId?: string;
}) {
  useMarkdownReady();
  const { text } = useTranslation();
  if (!thinkingText) return null;
  const line = running ? lastLine(thinkingText) : firstLine(thinkingText);
  return (
    <StepRow
      icon="thinking"
      expansionId={expansionId}
      title={text("Thinking", "思考")}
      note={short(plainNote(line))}
      running={running}
      copyText={thinkingText}
      inlineBody={<ThinkingBody text={thinkingText} />}
    />
  );
});

function parseParams(input?: string): Record<string, unknown> | undefined {
  if (!input) return undefined;
  try {
    const v = JSON.parse(input);
    if (v && typeof v === "object" && !Array.isArray(v)) {
      return v as Record<string, unknown>;
    }
    return { input: v };
  } catch {
    return { input: decodeEscapes(input) };
  }
}

/** A tool call's outcome as drawn. An explicit outcome stands; a call left
 *  without a result by a turn that was stopped or failed did not finish, so
 *  it reads as interrupted or with an unknown result, never as completed. */
export function toolOutcome(
  block: AssistantBlock,
  turnStatus?: string,
  running?: boolean,
): AssistantBlock["outcome"] {
  if (block.outcome || running || block.is_error || block.result != null) return block.outcome;
  if (isStopped(turnStatus)) return "cancelled";
  if (turnStatus === "error") return "unknown";
  return undefined;
}

/** 普通函数调用步骤：点行 → 右栏详情；有子调用时图标外圈表示可展开。
 *  running：流式中结果还没回来的调用——图标位换呼吸点。 */
export function FunctionStep({
  block,
  tree,
  running,
  sessionId,
  turnStatus,
  expansionId,
}: {
  expansionId?: string;
  block: AssistantBlock;
  tree?: TNode | null;
  running?: boolean;
  sessionId?: string;
  /** Status of the reply that owns the call. */
  turnStatus?: string;
}) {
  const [fullResult, setFullResult] = useState<string>();
  const [fullError, setFullError] = useState<string>();
  const [loadingFull, setLoadingFull] = useState(false);
  const { text } = useTranslation();
  const isError = !!block.is_error;
  const outcome = toolOutcome(block, turnStatus, running);
  const cancelled = outcome === "cancelled";
  const unknown = outcome === "unknown";
  const name = block.tool || "?";
  const kids = tree?.children || [];
  const result = fullResult ?? block.result;
  const view = presentTool(name, block.input, text);
  // A result bounded for the model: the card shows the head of the tool's
  // own output and says how long the whole output is.
  const fullLines = block.full_output?.total_lines;
  const fullNote = block.full_output && fullResult === undefined
    ? (fullLines
      ? text(`${fullLines.toLocaleString()} lines · full output saved`, `共 ${fullLines.toLocaleString()} 行，全文已保存`)
      : text("Full output saved", "全文已保存"))
    : undefined;
  const detailStatus = cancelled
    ? "cancelled"
    : running || loadingFull
    ? "running"
    : outcome === "waiting"
      ? "running"
      : outcome === "not_started" || unknown || isError
        ? "error"
        : "completed";
  const detail: DetailNode = {
    path: `chat-tool:${block.tool_call_id || name}`,
    session_id: sessionId,
    tree_root: tree,
    name: outcome === "not_started"
      ? `${name} (${text("denied before execution", "未开始执行")})`
      : unknown
        ? `${name} (${text("result unknown", "结果未知")})`
        : name,
    status: detailStatus,
    params: parseParams(block.input),
    output: result === undefined || result === null
      ? undefined : decodeEscapes(String(result)),
    error: fullError || (isError && result ? decodeEscapes(String(result)) : undefined),
  };
  async function openDetail() {
    const store = useSessionStore.getState();
    const messageId = block.message_id;
    const nodeId = block.node_id || block.tool_call_id;
    if (!(block.truncated || block.full_output) || fullResult !== undefined
        || !sessionId || !messageId || !nodeId) {
      store.showDetail(detail);
      return;
    }
    if (loadingFull) return;
    setFullError(undefined);
    setLoadingFull(true);
    store.showDetail({
      ...detail,
      status: "running",
      output: text("Loading...", "加载中..."),
      error: undefined,
    });
    try {
      const response = await fetchFullToolOutput(sessionId, messageId, nodeId);
      if (response && !response.error && response.result !== undefined) {
        setFullResult(typeof response.result === "object" ? JSON.stringify(response.result) : String(response.result));
      } else {
        setFullError(response?.error || text("Full tool output could not be loaded.", "无法加载完整工具输出。"));
      }
    } catch {
      setFullError(text("Full tool output could not be loaded.", "无法加载完整工具输出。"));
    } finally {
      setLoadingFull(false);
    }
  }
  return (
    <StepRow
      icon="function"
      expansionId={expansionId}
      glyph={view.icon}
      tone={view.tone}
      title={view.title}
      target={view.target ? short(view.target, 72) : undefined}
      note={result !== undefined && result !== null && result !== ""
        ? short(isGuiToolCall(name, detail.params)
          ? guiResultNote(name, decodeEscapes(String(result)), detailStatus, text, detail.params)
          : summarizeResult(decodeEscapes(String(result)), text), 90)
        : cancelled ? text("Interrupted", "已中断")
        : unknown ? text("Result unknown", "结果未知")
        : undefined}
      error={isError || cancelled || unknown}
      running={running || loadingFull}
      meta={fullNote}
      copyText={JSON.stringify(
        { tool: name, input: block.input, result }, null, 2)}
      detail={detail}
      onOpenDetail={() => void openDetail()}
      syncDetail={!loadingFull}
      subSteps={kids.length > 0
        ? kids.map((c, i) => <TreeStep key={c.path || i} node={c} sessionId={sessionId} />)
        : undefined}
    />
  );
}

/** A web search the model's provider ran itself: "Searched the web: <query>",
 *  running while the provider searches, then its sources as links in the
 *  row's inline body. Nothing ran locally, so there is no call tree. */
export function ProviderSearchStep({
  block,
  running,
  sessionId,
  turnStatus,
  expansionId,
}: {
  expansionId?: string;
  block: AssistantBlock;
  running?: boolean;
  sessionId?: string;
  turnStatus?: string;
}) {
  const { text } = useTranslation();
  const outcome = toolOutcome(block, turnStatus, running);
  const view = presentProviderSearch(block.input, text);
  const result = block.result == null ? undefined : String(block.result);
  const sources = parseSearchSources(result);
  const failed = !!block.is_error || outcome === "failed" || outcome === "not_started";
  const cancelled = outcome === "cancelled";
  const unknown = outcome === "unknown";
  const query = searchQuery(block.input);
  const detail: DetailNode = {
    path: `chat-tool:${block.tool_call_id || "web_search"}`,
    session_id: sessionId,
    name: "web_search",
    status: cancelled ? "cancelled" : running ? "running" : failed || unknown ? "error" : "completed",
    params: query ? { query } : undefined,
    output: result,
    error: failed ? result || text("Search failed", "搜索失败") : undefined,
  };
  const note = running ? undefined
    : failed ? (result && !sources.length ? short(result, 90) : text("Search failed", "搜索失败"))
    : cancelled ? text("Interrupted", "已中断")
    : unknown ? text("Result unknown", "结果未知")
    : sources.length === 1 ? text("1 source", "1 个来源")
    : sources.length ? text(`${sources.length} sources`, `${sources.length} 个来源`)
    : undefined;
  return (
    <StepRow
      icon="function"
      expansionId={expansionId}
      glyph={view.icon}
      tone={view.tone}
      title={view.title}
      note={note}
      error={failed || cancelled || unknown}
      running={running}
      copyText={[view.title, ...sources.map((s) => s.title ? `${s.title} — ${s.url}` : s.url)].join("\n")}
      detail={detail}
      inlineBody={sources.length ? <SearchSourceList sources={sources} /> : undefined}
    />
  );
}

/** context_tree / caller 链节点 → 递归步骤行。点行 → 右栏详情。
 *  defaultKidsOpen 只作用于当前节点，不向后代传播。 */
export function TreeStep({ node, actions, defaultKidsOpen, sessionId }: {
  node: TNode;
  actions?: React.ReactNode;
  defaultKidsOpen?: boolean;
  sessionId?: string;
}) {
  const { text } = useTranslation();
  // Subscribe so nested llm() block_delta updates re-render this row.
  useExecutionStreamStore((s) =>
    node.path ? s.byNodeId[node.path] : undefined,
  );
  const stream = streamPreviewForNode(node.path);
  const kids = node.children || [];
  const running = (node.status === "running" || stream.phase === "running"
      || stream.phase === "syncing")
    && !(node.duration_ms || node.end_time)
    && stream.phase !== "completed"
    && stream.phase !== "cancelled"
    && stream.phase !== "failed";
  const cancelled = node.status === "cancelled" || stream.phase === "cancelled";
  const isError = node.status === "error" || !!node.error || cancelled
    || stream.phase === "failed";
  const noteParts: string[] = [];
  const params = node.params
    ? Object.fromEntries(Object.entries(node.params)
        .filter(([k]) => k !== "runtime" && k !== "callback"))
    : undefined;
  // Prompt is secondary (design §12).
  const promptPreview = params && typeof params._content === "string"
    ? String(params._content)
    : undefined;
  if (promptPreview && Object.keys(params || {}).length) {
    noteParts.push(short({ prompt: promptPreview }, 48));
  } else if (params && Object.keys(params).length) {
    noteParts.push(short(params, 70));
  }
  if (node.duration_ms) {
    const ms = Math.round(node.duration_ms);
    noteParts.push(ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`);
  }
  if (cancelled) noteParts.push(text("cancelled", "已取消"));
  if (stream.syncing) noteParts.push(text("resyncing…", "重新同步"));
  if (stream.attemptLabel) {
    noteParts.push(
      stream.attemptLabel === "resync"
        ? text("resyncing…", "重新同步")
        : text(`retrying (${stream.attemptLabel})`, `重试中（${stream.attemptLabel}）`),
    );
  }
  // Growing reply is primary while running; final output once terminal.
  const liveText = stream.text || node.stream_preview || "";
  const outRaw = node.error
    || (running ? (liveText || node.output || node.raw_reply) : (node.output ?? node.raw_reply ?? liveText));
  const out = (outRaw === undefined || outRaw === null
    || String(outRaw).trim() === "" || String(outRaw).trim() === "null")
    ? undefined : decodeEscapes(typeof outRaw === "object" ? JSON.stringify(outRaw) : String(outRaw));
  const reasoning = stream.reasoning || node.stream_reasoning || "";
  const detail: DetailNode = {
    path: node.path || `chat-node:${node.name || "call"}`,
    session_id: sessionId,
    name: node.name || node.node_type || "call",
    status: node.status || (isError ? "error" : "completed"),
    params,
    output: node.error ? undefined : out,
    error: node.error ? decodeEscapes(String(node.error)) : undefined,
    duration_ms: node.duration_ms,
    node_type: node.node_type,
    raw_reply: out || node.raw_reply,
    tree_root: node,
    // Foldable reasoning — opaque signatures never included in stream.reasoning.
    ...(reasoning ? { thinking: reasoning } : {}),
  };
  const isLlm = node.node_type === "exec" || node.name === "LLM";
  const guiStep = !!node.name && isGuiToolCall(node.name, params);
  if (out && !node.error && guiStep) {
    noteParts.push(short(guiResultNote(node.name!, out, node.status || "", text, params), 60));
  } else if (out && !node.error) noteParts.push("→ " + short(out, 60));
  else if (isLlm && running && reasoning && !out) {
    noteParts.push(text("thinking…", "思考中") + " · " + short(reasoning, 40));
  }
  if (isLlm && !out && !running && !isError) {
    noteParts.push(text("No text output", "无文本输出"));
  }
  const treeView = isLlm || !node.name ? undefined : presentTool(node.name, params ? JSON.stringify(params) : undefined, text);
  const contentBody = isLlm ? (
    <LlmNodeContent
      nodeId={node.path}
      treeRoot={node}
      fallbackText={out || null}
      fallbackThinking={reasoning || null}
    />
  ) : undefined;
  return (
    <StepRow
      expansionId={node.path ? `node:${node.path}` : undefined}
      icon={isLlm ? "llm" : "function"}
      glyph={isLlm ? undefined : treeView?.icon}
      tone={isLlm ? undefined : treeView?.tone}
      title={isLlm ? "LLM" : (guiStep ? treeView?.title : node.name) || node.node_type || "call"}
      target={guiStep && treeView?.target ? short(treeView.target, 72) : undefined}
      note={noteParts.join(" · ")}
      error={isError}
      running={running}
      actions={actions}
      copyText={isLlm ? (out || "") : JSON.stringify(
        { name: node.name, params: node.params, output: node.output ?? node.raw_reply, error: node.error },
        null, 2)}
      detail={detail}
      inlineBody={contentBody}
      stateKey={node.path ? `llm:${node.path}` : undefined}
      subSteps={!isLlm && kids.length > 0
        ? kids.map((c, i) => (
            <TreeStep key={c.path || i} node={c} sessionId={sessionId} />
          ))
        : undefined}
      defaultKidsOpen={defaultKidsOpen}
    />
  );
}

/** 子代理步骤：轮次里子代理唯一的显示处。状态在摘要位；点行内联展开它
 *  自己的对话（任务、步骤、回复，运行中实时）；Open ↗ 把主视图切到它最新
 *  的回复；运行中可取消。 */
export function SubAgentStep({ card, expansionId }: { card: ChatMsg; expansionId?: string }) {
  const { text } = useTranslation();
  const view = useTranscriptViewHere();
  const [chosen, setOpen] = useChosenOpen(expansionId ?? `card:${card.id}`);
  const open = chosen ?? stepOpenByDefault(view, "subagent");
  // The agent's newest reply, from its loaded branch: later messages to the
  // agent move it past the head its spawn card recorded.
  const [tip, setTip] = useState<string | null>(null);
  const attach = card.attach || {};
  const name = (attach.label || "").trim()
    || (attach.head_id || "").slice(0, 8)
    || text("Sub-agent", "子代理");
  const status = attach.status;
  const running = isAgentRunning(status);
  const cancelling = status === "cancelling";
  const isError = status === "errored";
  const statusNote = cancelling
    ? text("cancelling…", "正在取消")
    : running
    ? text("running…", "运行中…")
    : status === "cancelled"
      ? text("cancelled", "已取消")
      : isError
        ? text("errored", "出错")
        : text("completed", "已完成");
  const targetSessionId = attach.session_id || "";
  const targetHead = attach.head_id || "";
  // Show the agent's own conversation in the main view at its newest reply;
  // the sub-agent branch banner there leads back. While the conversation
  // runs its head cannot move, so the agent opens here in the row instead.
  async function goTo(e: React.MouseEvent) {
    e.stopPropagation();
    if (!targetSessionId) return;
    const target = tip
      || await loadSubAgentTranscript(targetSessionId, subAgentKey(card), {
        jobId: attach.job_id || undefined,
        headId: targetHead || undefined,
      })
      || targetHead;
    const moved = target ? await checkoutBranch(targetSessionId, target) : { ok: false };
    if (!moved.ok) setOpen(true);
  }
  function cancel(e: React.MouseEvent) {
    e.stopPropagation();
    const executionId = attach.execution_id;
    const expectedVersion = attach.status_version;
    if (executionId && typeof expectedVersion === "number") {
      sendExecutionCancel(sendRuntimeCommand, executionId, expectedVersion);
    }
  }
  const preview = card.content || "";
  const detail: DetailNode = {
    path: `spawn:${targetHead || card.id}`,
    name: `${text("Sub-agent", "子代理")}: ${name}`,
    status: status || "completed",
    output: preview || undefined,
    prompt: attach.prompt,
  };
  return (
    <StepRow
      icon="subagent"
      expansionId={expansionId ?? `card:${card.id}`}
      title={`${text("Sub-agent", "子代理")}: ${name}`}
      note={statusNote}
      running={running}
      error={isError}
      detail={detail}
      dataMsgId={card.id}
      dataHeadId={targetHead || undefined}
      open={open}
      onOpenChange={setOpen}
      inlineBody={targetSessionId
        ? <SubAgentTranscript card={card} sessionId={targetSessionId} onTip={setTip} />
        : undefined}
      meta={<MessageTimestamp timestamp={card.timestamp} />}
      actions={
        <>
          {(running || cancelling)
            && attach.execution_id
            && typeof attach.status_version === "number" ? (
            <button
              type="button"
              className="tl-btn"
              onClick={cancel}
              disabled={cancelling}
              aria-label={cancelling ? text("Cancelling…", "正在取消") : text("Cancel execution", "取消运行")}
            >
              <Square size={12} aria-hidden="true" />
              <span className="tl-btn-label">
                {cancelling
                  ? text("Cancelling…", "正在取消")
                  : text("Cancel execution", "取消运行")}
              </span>
            </button>
          ) : null}
          {targetSessionId ? (
            <button
              type="button"
              className="tl-btn"
              onClick={(e) => void goTo(e)}
              aria-label={text("Open", "打开对话")}
            >
              <ArrowUpRight size={12} aria-hidden="true" />
              <span className="tl-btn-label">{text("Open", "打开对话")}</span>
            </button>
          ) : null}
        </>
      }
    />
  );
}

/** Sub-agents with no tool step of their own, as the timeline a tool-started
 *  agent draws in: one summary row, and a step per agent when opened. */
export function SubAgentStrip({ cards, streaming, sessionId, expansionId }: {
  cards: ChatMsg[];
  streaming?: boolean;
  sessionId?: string;
  expansionId?: string;
}) {
  const { text } = useTranslation();
  const facts = spawnFacts(cards, text);
  return (
    <ExecutionStrip
      expansionId={expansionId}
      streaming={streaming}
      subagentHeads={facts.spawnHeads}
      label={summarizeToolGroup([], { text, ...facts, sessionId })}
    >
      {cards.map((card) => <SubAgentStep key={card.id} card={card} />)}
    </ExecutionStrip>
  );
}
