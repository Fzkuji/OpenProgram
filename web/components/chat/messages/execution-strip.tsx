"use client";

/**
 * 执行时间线 — docs/design/ui/chat-turn-visual-spec.html 的实现。
 *
 * 分工：聊天里只画**调用树结构**（行 + 层级），详情看右栏——点击函数
 * / 子代理行调 `showDetail`，右栏 Executions 面板显示输入/输出/耗时
 * （与 ExecutionDag、ToolsBlock 同一机制）。行尾 ⌄N 只展开子行
 * （递归层级），不在聊天里倒 JSON。思考行例外：内容轻，点行内联展开。
 *
 * 图标统一走 animated-icons 系列（Brain / Wrench / Bot），行悬停播放
 * 动画；图标绝对定位在标题行内部，天然与文字对齐。流式进行中的一轮
 * 不走这里（assistant-bubble 平铺实时块），落定后切到本组件。
 */
import { useRef, useState } from "react";

import type { AssistantBlock, ChatMsg, DetailNode } from "@/lib/session-store";
import { useSessionStore } from "@/lib/session-store";
import type { TNode } from "./execution-dag/types";
import { useTranslation } from "@/lib/i18n";
import { renderMarkdown, useMarkdownReady } from "./markdown";
import {
  type AnimatedNavIconHandle,
  BotIcon,
  BrainIcon,
  WrenchIcon,
} from "@/components/animated-icons";

/** spawn 类工具：在摘要里算"子代理"，不算普通函数调用。 */
export const SPAWNING_TOOL_NAMES = new Set(["task", "message_branch"]);

function wsSend(payload: unknown): void {
  const w = window as unknown as { ws?: WebSocket };
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}

/** 汇总标签只分三类，不报具体工具名。 */
export function execStripLabel(
  blocks: AssistantBlock[],
  spawnNames: string[],
  text: (en: string, zh: string) => string,
): string {
  let thinking = 0;
  let functions = 0;
  let spawnBlocks = 0;
  for (const b of blocks) {
    if (b.type === "thinking") thinking++;
    else if (b.type === "tool") {
      if (SPAWNING_TOOL_NAMES.has(b.tool || "")) spawnBlocks++;
      else functions++;
    }
  }
  const parts: string[] = [];
  if (thinking > 0) parts.push(`${text("thinking", "思考")} ×${thinking}`);
  if (functions > 0) parts.push(`${text("functions", "函数")} ×${functions}`);
  const subAgents = Math.max(spawnBlocks, spawnNames.length);
  if (subAgents > 0) {
    parts.push(
      spawnNames.length > 0
        ? `${text("sub-agent", "子代理")}: ${spawnNames.join("、")}`
        : `${text("sub-agent", "子代理")} ×${subAgents}`,
    );
  }
  return parts.join(" · ") || text("execution", "执行过程");
}

/** 时间线外壳：一行淡文字摘要 ›，点击展开竖线时间线。 */
export function ExecutionStrip({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const { text } = useTranslation();
  return (
    <div className="tl" data-open={open ? "1" : "0"}>
      <button
        type="button"
        className="tl-toggle"
        onClick={() => setOpen((o) => !o)}
        title={open
          ? text("Collapse execution trace", "收起执行过程")
          : text("Expand execution trace", "展开执行过程")}
      >
        <span>{label}</span>
        <span className="tl-chev" aria-hidden="true">›</span>
      </button>
      {open ? <div className="tl-body">{children}</div> : null}
    </div>
  );
}

/** 后端 ensure_ascii 序列化出的 \\uXXXX 转义还原成真实字符。 */
function decodeEscapes(s: string): string {
  if (!s.includes("\\u")) return s;
  return s.replace(/\\u([0-9a-fA-F]{4})/g,
    (_, h) => String.fromCharCode(parseInt(h, 16)));
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

/** 单个步骤行：图标锚在标题行里压竖线 + 标题 + 单行摘要 + 悬停动作。
 *
 *  两种点击语义（互斥）：
 *  - `detail`：点行 → 右栏 Executions 显示详情（函数 / 子代理行）。
 *  - `inlineBody`：点行 → 内联展开（思考行）。
 *  `subSteps`（子调用层级）**常驻展开**：轮级摘要行已是折叠开关，
 *  树一旦展开就完整可见，行上不放任何展开控件（用户裁决：别加箭头）。 */
export function StepRow({
  icon,
  title,
  note,
  error,
  running,
  actions,
  copyText,
  detail,
  inlineBody,
  subSteps,
}: {
  icon: "thinking" | "function" | "subagent";
  title: string;
  note?: string;
  error?: boolean;
  running?: boolean;
  actions?: React.ReactNode;
  copyText?: string;
  detail?: DetailNode;
  inlineBody?: React.ReactNode;
  subSteps?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  const { text } = useTranslation();
  function copy(e: React.MouseEvent) {
    e.stopPropagation();
    const done = () => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    };
    if (copyText && navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(copyText).then(done, done);
    } else done();
  }
  function onHeadClick() {
    if (detail) {
      useSessionStore.getState().showDetail(detail);
      return;
    }
    if (inlineBody) setOpen((v) => !v);
  }
  const Icon = icon === "thinking" ? BrainIcon
    : icon === "subagent" ? BotIcon : WrenchIcon;
  return (
    <div className={"tl-step" + (open ? " open" : "")}>
      <div
        className="tl-step-head"
        onClick={onHeadClick}
        onMouseEnter={() => iconRef.current?.startAnimation?.()}
        onMouseLeave={() => iconRef.current?.stopAnimation?.()}
        style={detail || inlineBody ? undefined : { cursor: "default" }}
      >
        <span
          className={
            "tl-step-icon"
            + (error ? " is-error" : "")
            + (running ? " is-running" : "")
          }
          aria-hidden="true"
        >
          {running ? (
            <span className="tl-spin" />
          ) : error ? (
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none"
                 stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="6" y1="6" x2="18" y2="18" />
              <line x1="18" y1="6" x2="6" y2="18" />
            </svg>
          ) : (
            <Icon ref={iconRef} size={13} />
          )}
        </span>
        <span className={"tl-step-title" + (error ? " is-error" : "")}>{title}</span>
        {note ? <span className="tl-step-note" title={note}>{note}</span> : null}
        <span className="tl-step-act">
          {copyText ? (
            <button type="button" className="tl-btn" onClick={copy}>
              {copied ? text("Copied", "已复制") : text("Copy", "复制")}
            </button>
          ) : null}
          {actions}
        </span>
      </div>
      {open && inlineBody ? <div className="tl-step-body">{inlineBody}</div> : null}
      {subSteps ? <div className="tl-sub">{subSteps}</div> : null}
    </div>
  );
}

/** 思考步骤：内容轻，点行内联展开。 */
export function ThinkingStep({ text: thinkingText }: { text: string }) {
  useMarkdownReady();
  const { text } = useTranslation();
  if (!thinkingText) return null;
  return (
    <StepRow
      icon="thinking"
      title={text("Thinking", "思考")}
      note={short(firstLine(thinkingText))}
      copyText={thinkingText}
      inlineBody={
        <div
          className="chat-text"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(thinkingText) }}
        />
      }
    />
  );
}

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

/** 普通函数调用步骤：点行 → 右栏详情；子调用层级用 ⌄N 展开。 */
export function FunctionStep({
  block,
  tree,
}: {
  block: AssistantBlock;
  tree?: TNode | null;
}) {
  const { text } = useTranslation();
  const isError = !!block.is_error;
  const name = block.tool || "?";
  const kids = tree?.children || [];
  const detail: DetailNode = {
    path: `chat-tool:${block.tool_call_id || name}`,
    name,
    status: isError ? "error" : "completed",
    params: parseParams(block.input),
    output: block.result === undefined || block.result === null
      ? undefined : decodeEscapes(String(block.result)),
    error: isError && block.result
      ? decodeEscapes(String(block.result)) : undefined,
  };
  return (
    <StepRow
      icon="function"
      title={text("Function call", "函数调用")}
      note={`${name}${block.input ? " · " + short(block.input) : ""}`}
      error={isError}
      copyText={JSON.stringify(
        { tool: name, input: block.input, result: block.result }, null, 2)}
      detail={detail}
      subSteps={kids.length > 0
        ? kids.map((c, i) => <TreeStep key={c.path || i} node={c} />)
        : undefined}
    />
  );
}

/** context_tree / caller 链节点 → 递归步骤行。点行 → 右栏详情。 */
export function TreeStep({ node }: { node: TNode }) {
  const kids = node.children || [];
  const running = node.status === "running"
    && !(node.duration_ms || node.end_time);
  const isError = node.status === "error" || !!node.error;
  const noteParts: string[] = [];
  const params = node.params
    ? Object.fromEntries(Object.entries(node.params)
        .filter(([k]) => k !== "runtime" && k !== "callback"))
    : undefined;
  if (params && Object.keys(params).length) noteParts.push(short(params, 70));
  if (node.duration_ms) noteParts.push(`${Math.round(node.duration_ms)}ms`);
  const outRaw = node.error || node.output;
  const out = (outRaw === undefined || outRaw === null
    || String(outRaw).trim() === "" || String(outRaw).trim() === "null")
    ? undefined : decodeEscapes(String(outRaw));
  const detail: DetailNode = {
    path: node.path || `chat-node:${node.name || "call"}`,
    name: node.name || node.node_type || "call",
    status: node.status || (isError ? "error" : "completed"),
    params,
    output: node.error ? undefined : out,
    error: node.error ? decodeEscapes(String(node.error)) : undefined,
    duration_ms: node.duration_ms,
    node_type: node.node_type,
    raw_reply: node.raw_reply,
  };
  return (
    <StepRow
      icon="function"
      title={node.name || node.node_type || "call"}
      note={noteParts.join(" · ")}
      error={isError}
      running={running}
      copyText={JSON.stringify(
        { name: node.name, params: node.params, output: node.output, error: node.error },
        null, 2)}
      detail={detail}
      subSteps={kids.length > 0
        ? kids.map((c, i) => <TreeStep key={c.path || i} node={c} />)
        : undefined}
    />
  );
}

/** 子代理步骤：状态在摘要位，Switch/取消在动作位，点行 → 右栏详情。 */
export function SubAgentStep({ card }: { card: ChatMsg }) {
  const { text } = useTranslation();
  const attach = card.attach || {};
  const name = (attach.label || "").trim()
    || (attach.head_id || "").slice(0, 8)
    || text("sub-agent", "子代理");
  const status = attach.status;
  const running = status === "running" || status === "pending" || status === "queued";
  const isError = status === "errored";
  const statusNote = running
    ? text("running…", "运行中…")
    : status === "cancelled"
      ? text("cancelled", "已取消")
      : isError
        ? text("errored", "出错")
        : text("completed", "已完成");
  const targetSessionId = attach.session_id || "";
  const targetHead = attach.head_id || "";
  function switchTo(e: React.MouseEvent) {
    e.stopPropagation();
    if (!targetSessionId || !targetHead) return;
    wsSend({
      action: "checkout_branch",
      session_id: targetSessionId,
      head_msg_id: targetHead,
    });
    wsSend({ action: "load_session", session_id: targetSessionId });
  }
  function cancel(e: React.MouseEvent) {
    e.stopPropagation();
    if (attach.task_id) wsSend({ action: "cancel_task", task_id: attach.task_id });
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
      title={`${text("Sub-agent", "子代理")}: ${name}`}
      note={statusNote}
      running={running}
      error={isError}
      detail={detail}
      actions={
        <>
          {running && attach.task_id ? (
            <button type="button" className="tl-btn" onClick={cancel}>
              {text("Cancel", "取消")}
            </button>
          ) : null}
          {targetHead ? (
            <button type="button" className="tl-btn" onClick={switchTo}>
              Switch ↗
            </button>
          ) : null}
        </>
      }
    />
  );
}
