"use client";

/**
 * 执行时间线 — docs/design/ui/chat-turn-visual-spec.html 的实现。
 *
 * 一轮里连续的 thinking/tool 块（含子代理）渲染成 GPT 活动流式的
 * 时间线：外层无框，收起态是一行淡文字摘要（思考 ×N · 函数 ×N ·
 * 子代理: 名字 ›），展开后是一根竖线串起的步骤行——圆形图标压线、
 * 标题 + 单行摘要、动作悬停出现、点行展开内容。函数内部还有子调用
 * 时（@agentic_function 的 context_tree），展开是缩进一层的子时间
 * 线，同一套行样式递归，层数不限。
 *
 * 流式进行中的一轮不走这里（assistant-bubble 平铺实时块），落定后
 * 切到本组件。
 */
import { useState } from "react";

import type { AssistantBlock, ChatMsg } from "@/lib/session-store";
import type { TNode } from "./execution-dag/types";
import { useTranslation } from "@/lib/i18n";
import { renderMarkdown, useMarkdownReady } from "./markdown";
import { FunctionIcon, ThinkingIcon } from "./step-icons";

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

/** 单个步骤行：图标压线 + 标题 + 单行摘要 + 悬停动作 + 可展开内容。 */
export function StepRow({
  icon,
  title,
  note,
  error,
  running,
  actions,
  copyText,
  children,
  defaultOpen,
}: {
  icon: "thinking" | "function" | "subagent";
  title: string;
  note?: string;
  error?: boolean;
  running?: boolean;
  actions?: React.ReactNode;
  /** 悬停"复制"按钮复制的内容；不传则无复制按钮。 */
  copyText?: string;
  children?: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(!!defaultOpen);
  const [copied, setCopied] = useState(false);
  const { text } = useTranslation();
  const expandable = !!children;
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
  return (
    <div className={"tl-step" + (open ? " open" : "")}>
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
          <svg viewBox="0 0 24 24"><line x1="6" y1="6" x2="18" y2="18" /><line x1="18" y1="6" x2="6" y2="18" /></svg>
        ) : icon === "thinking" ? (
          <ThinkingIcon />
        ) : icon === "subagent" ? (
          <svg viewBox="0 0 24 24"><polyline points="4 17 10 11 4 5" /><line x1="12" y1="19" x2="20" y2="19" /></svg>
        ) : (
          <FunctionIcon />
        )}
      </span>
      <div
        className="tl-step-head"
        onClick={expandable ? () => setOpen((v) => !v) : undefined}
        style={expandable ? undefined : { cursor: "default" }}
      >
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
      {open && children ? <div className="tl-step-body">{children}</div> : null}
    </div>
  );
}

function firstLine(s: string): string {
  const t = (s || "").trim();
  const nl = t.indexOf("\n");
  return nl > 0 ? t.slice(0, nl) : t;
}

function short(v: unknown, n = 90): string {
  let s: string;
  if (typeof v === "string") s = v;
  else {
    try { s = JSON.stringify(v); } catch { s = String(v); }
  }
  s = (s || "").replace(/\s+/g, " ").trim();
  return s.length > n ? s.slice(0, n) + "…" : s;
}

/** 思考步骤。 */
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
    >
      <div
        className="chat-text"
        dangerouslySetInnerHTML={{ __html: renderMarkdown(thinkingText) }}
      />
    </StepRow>
  );
}

/** 普通函数调用步骤（可带 context_tree 递归子层级）。 */
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
  const copyText = JSON.stringify(
    { tool: name, input: block.input, result: block.result }, null, 2);
  const kids = tree?.children || [];
  const hasBody = !!block.input || block.result !== undefined || kids.length > 0;
  return (
    <StepRow
      icon="function"
      title={text("Function call", "函数调用")}
      note={`${name}${block.input ? " · " + short(block.input) : ""}`}
      error={isError}
      copyText={copyText}
    >
      {hasBody ? (
        <>
          {block.input ? (
            <div className="tl-mono">{short(block.input, 4000)}</div>
          ) : null}
          {block.result !== undefined && block.result !== null && block.result !== "" ? (
            <div className="tl-mono tl-result">{short(String(block.result), 4000)}</div>
          ) : null}
          {kids.length > 0 ? (
            <div className="tl-sub">
              {kids.map((c, i) => <TreeStep key={c.path || i} node={c} />)}
            </div>
          ) : null}
        </>
      ) : null}
    </StepRow>
  );
}

/** context_tree 节点 → 递归步骤行（函数调函数的层级）。 */
export function TreeStep({ node }: { node: TNode }) {
  const kids = node.children || [];
  const running = node.status === "running"
    && !(node.duration_ms || node.end_time);
  const isError = node.status === "error" || !!node.error;
  const noteParts: string[] = [];
  const params = node.params
    ? Object.entries(node.params).filter(([k]) => k !== "runtime" && k !== "callback")
    : [];
  if (params.length) noteParts.push(short(Object.fromEntries(params), 70));
  if (node.duration_ms) noteParts.push(`${Math.round(node.duration_ms)}ms`);
  const out = node.error || node.output;
  const hasBody = out !== undefined && out !== null && out !== "" || kids.length > 0;
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
    >
      {hasBody ? (
        <>
          {out !== undefined && out !== null && out !== "" ? (
            <div className={"tl-mono" + (isError ? " is-error" : "")}>
              {short(out, 4000)}
            </div>
          ) : null}
          {kids.length > 0 ? (
            <div className="tl-sub">
              {kids.map((c, i) => <TreeStep key={c.path || i} node={c} />)}
            </div>
          ) : null}
        </>
      ) : null}
    </StepRow>
  );
}

/** 子代理步骤：状态在摘要位，Switch 在动作位，展开是回流预览。 */
export function SubAgentStep({ card }: { card: ChatMsg }) {
  useMarkdownReady();
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
  return (
    <StepRow
      icon="subagent"
      title={`${text("Sub-agent", "子代理")}: ${name}`}
      note={statusNote}
      running={running}
      error={isError}
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
    >
      {preview ? (
        <div
          className="chat-text"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(preview) }}
        />
      ) : null}
    </StepRow>
  );
}
