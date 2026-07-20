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
import { useEffect, useState } from "react";

import type { AssistantBlock, ChatMsg, DetailNode } from "@/lib/session-store";
import { useSessionStore } from "@/lib/session-store";
import type { TNode } from "./tree-types";
import { useTranslation } from "@/lib/i18n";
import { renderMarkdown, useMarkdownReady } from "./markdown";
import {
  BotIcon,
  BrainIcon,
  CpuIcon,
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
  if (thinking > 0) parts.push(`${text("Thinking", "思考")} ×${thinking}`);
  if (functions > 0) parts.push(`${text("Functions", "函数")} ×${functions}`);
  const subAgents = Math.max(spawnBlocks, spawnNames.length);
  if (subAgents > 0) {
    parts.push(
      spawnNames.length > 0
        ? `${text("Sub-agent", "子代理")}: ${spawnNames.join("、")}`
        : `${text("Sub-agent", "子代理")} ×${subAgents}`,
    );
  }
  return parts.join(" · ") || text("Execution", "执行过程");
}

/** 高度过渡容器：grid 0fr↔1fr 动画，展开向下推、收起平滑抽走。
 *  关闭动画结束后卸载子树，折叠状态不付渲染成本。 */
function Collapse({ open, children }: {
  open: boolean;
  children: React.ReactNode;
}) {
  const [shown, setShown] = useState(false);
  const [mounted, setMounted] = useState(open);
  useEffect(() => {
    if (open) {
      setMounted(true);
      const raf = requestAnimationFrame(() =>
        requestAnimationFrame(() => setShown(true)));
      return () => cancelAnimationFrame(raf);
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
 *  流式进行中默认**展开**：助手干活时用户要能直接看到步骤在往下长，
 *  而不是盯着一条什么都不说的摘要条。小字摘要仍加 shimmer 扫光，新步骤
 *  在底部拼接，运行中的行用呼吸点。
 *
 *  落定后不强制收起——用户在流式期间的手动折叠/展开一律保留（收起后
 *  又冒出新步骤也不会被强行掀开）。只有"从没手动点过 + 流已结束"这一种
 *  情况回到折叠态，也就是历史消息的默认样子。 */
export function ExecutionStrip({
  label,
  streaming,
  children,
}: {
  label: string;
  streaming?: boolean;
  children: React.ReactNode;
}) {
  const [userSet, setUserSet] = useState<boolean | null>(null);
  const open = userSet ?? !!streaming;
  const setOpen = (next: (o: boolean) => boolean) => setUserSet(next(open));
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
        <span className={streaming ? "tl-label-shimmer" : undefined}>{label}</span>
        <span className="tl-chev" aria-hidden="true">›</span>
      </button>
      <Collapse open={open}>
        <div className="tl-body">{children}</div>
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
 *    内联全文；两者都没有的叶子行，点空白也进右栏。
 *  图标静态不动画；三类配色区分（思考紫 / 函数橙 / LLM 青 / 子代理绿）；
 *  折叠时行尾淡字 "⋯ N 步" 提示。 */
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
  subCount,
  defaultKidsOpen,
}: {
  icon: "thinking" | "function" | "llm" | "subagent";
  title: string;
  note?: string;
  error?: boolean;
  running?: boolean;
  actions?: React.ReactNode;
  copyText?: string;
  detail?: DetailNode;
  inlineBody?: React.ReactNode;
  subSteps?: React.ReactNode;
  subCount?: number;
  /** 子树初始展开（手动函数运行的根行用：过程即内容，不折叠）。 */
  defaultKidsOpen?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [kidsOpen, setKidsOpen] = useState(!!defaultKidsOpen);
  const [copied, setCopied] = useState(false);
  const { text } = useTranslation();
  const toggleable = !!subSteps || !!inlineBody;
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
  function toggle() {
    if (subSteps) setKidsOpen((v) => !v);
    else if (inlineBody) setOpen((v) => !v);
    else if (detail) useSessionStore.getState().showDetail(detail);
  }
  function openDetail(e: React.MouseEvent) {
    if (!detail) return;
    e.stopPropagation();
    useSessionStore.getState().showDetail(detail);
  }
  const Icon = icon === "thinking" ? BrainIcon
    : icon === "subagent" ? BotIcon
    : icon === "llm" ? CpuIcon : WrenchIcon;
  return (
    <div className={"tl-step" + (open ? " open" : "")}>
      <div
        className="tl-step-head"
        onClick={toggle}
        style={toggleable || detail ? undefined : { cursor: "default" }}
      >
        <span
          className={
            "tl-step-icon k-" + icon
            + (error ? " is-error" : "")
            + (running ? " is-running" : "")
            + (toggleable ? " is-toggleable" : "")
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
            <Icon size={13} />
          )}
        </span>
        <span
          className={"tl-step-title" + (error ? " is-error" : "")
            + (detail ? " tl-step-link" : "")}
          onClick={detail ? openDetail : undefined}
          title={detail ? text("Show details in the side panel", "右栏看详情") : undefined}
        >
          {title}
        </span>
        {/* 不加 title：流式中 note 每个 delta 都在变，原生 tooltip 会
            钉死在视口角落变成"漂浮黑框"；全文本来就有行内展开可看。 */}
        {note ? <span className="tl-step-note">{note}</span> : null}
        {subSteps && !kidsOpen ? (
          <span className="tl-fold-hint">
            {`⋯ ${subCount || ""} ${text("steps", "步")}`.replace("  ", " ")}
          </span>
        ) : null}
        <span className="tl-step-act">
          {copyText ? (
            <button type="button" className="tl-btn" onClick={copy}>
              {copied ? text("Copied", "已复制") : text("Copy", "复制")}
            </button>
          ) : null}
          {actions}
        </span>
      </div>
      {inlineBody ? (
        <Collapse open={open}>
          <div className="tl-step-body">{inlineBody}</div>
        </Collapse>
      ) : null}
      {subSteps ? (
        <Collapse open={kidsOpen}>
          <div className="tl-sub">{subSteps}</div>
        </Collapse>
      ) : null}
    </div>
  );
}

/** 思考步骤：内容轻，点行内联展开。
 *  流式中（running）note 显示**最新**一行——最近在想什么；落定后显示
 *  第一行作固定摘要。展开态的全文随 delta 向下拼接生长。 */
export function ThinkingStep({ text: thinkingText, running }: {
  text: string;
  running?: boolean;
}) {
  useMarkdownReady();
  const { text } = useTranslation();
  if (!thinkingText) return null;
  const line = running ? lastLine(thinkingText) : firstLine(thinkingText);
  return (
    <StepRow
      icon="thinking"
      title={text("Thinking", "思考")}
      note={short(plainNote(line))}
      running={running}
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

/** 普通函数调用步骤：点行 → 右栏详情；子调用层级用 ⌄N 展开。
 *  running：流式中结果还没回来的调用——图标位换呼吸点。 */
export function FunctionStep({
  block,
  tree,
  running,
}: {
  block: AssistantBlock;
  tree?: TNode | null;
  running?: boolean;
}) {
  const { text } = useTranslation();
  const isError = !!block.is_error;
  const name = block.tool || "?";
  const kids = tree?.children || [];
  const detail: DetailNode = {
    path: `chat-tool:${block.tool_call_id || name}`,
    name,
    status: running ? "running" : isError ? "error" : "completed",
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
      note={`${name}${block.input ? " · " + short(block.input, 60) : ""}${
        block.result !== undefined && block.result !== null && block.result !== ""
          ? " → " + short(String(block.result), 60) : ""}`}
      error={isError}
      running={running}
      copyText={JSON.stringify(
        { tool: name, input: block.input, result: block.result }, null, 2)}
      detail={detail}
      subSteps={kids.length > 0
        ? kids.map((c, i) => <TreeStep key={c.path || i} node={c} />)
        : undefined}
      subCount={kids.length || undefined}
    />
  );
}

/** context_tree / caller 链节点 → 递归步骤行。点行 → 右栏详情。
 *  actions/defaultKidsOpen 供手动运行的根行透传（重试、版本切换）。 */
export function TreeStep({ node, actions, defaultKidsOpen }: {
  node: TNode;
  actions?: React.ReactNode;
  defaultKidsOpen?: boolean;
}) {
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
  const isLlm = node.node_type === "exec" || node.name === "LLM";
  if (out && !node.error) noteParts.push("→ " + short(out, 60));
  return (
    <StepRow
      icon={isLlm ? "llm" : "function"}
      title={isLlm ? "LLM" : (node.name || node.node_type || "call")}
      note={noteParts.join(" · ")}
      error={isError}
      running={running}
      actions={actions}
      copyText={JSON.stringify(
        { name: node.name, params: node.params, output: node.output, error: node.error },
        null, 2)}
      detail={detail}
      subSteps={kids.length > 0
        ? kids.map((c, i) => (
            <TreeStep key={c.path || i} node={c} defaultKidsOpen={defaultKidsOpen} />
          ))
        : undefined}
      subCount={kids.length || undefined}
      defaultKidsOpen={defaultKidsOpen}
    />
  );
}

/** 子代理步骤：状态在摘要位，Switch/取消在动作位，点行 → 右栏详情。 */
export function SubAgentStep({ card }: { card: ChatMsg }) {
  const { text } = useTranslation();
  const attach = card.attach || {};
  const name = (attach.label || "").trim()
    || (attach.head_id || "").slice(0, 8)
    || text("Sub-agent", "子代理");
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
