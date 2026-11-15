"use client";

/**
 * Ordered chat-like content for a nested LLM DAG node.
 * Reuses markdown / thinking fold / function-row visuals.
 * tool_ref rows point at real tree children — no duplicated tool results.
 */
import { memo, useMemo, useState } from "react";
import { useExecutionStreamStore } from "@/lib/execution-stream/store";
import type { StreamAttempt, StreamBlock } from "@/lib/execution-stream/types";
import { useTranslation } from "@/lib/i18n";
import { useSessionStore, type DetailNode } from "@/lib/session-store";
import { renderMarkdown, useMarkdownReady } from "../bubbles/markdown";
import type { TNode } from "./tree-types";

const MAX_LLM_CONTENT_DEPTH = 8;

function usePersistentOpen(key: string, defaultOpen = false) {
  const saved = useSessionStore((s) => s.llmContentOpen[key]);
  const setOpen = useSessionStore((s) => s.setLlmContentOpen);
  const open = saved === undefined ? defaultOpen : saved;
  return [open, (next: boolean) => setOpen(key, next)] as const;
}

export function findExecutionNode(root: TNode | undefined, id: string | undefined): TNode | undefined {
  if (!root || !id) return undefined;
  const stack: TNode[] = [root];
  while (stack.length) {
    const n = stack.pop()!;
    // Tool refs carry a DAG node id. Matching a display name could resolve
    // the wrong sibling when two calls use the same function name.
    if (n.path === id) return n;
    for (const c of n.children || []) stack.push(c);
  }
  return undefined;
}

function MarkdownBody({ text, running }: { text: string; running?: boolean }) {
  useMarkdownReady();
  if (!text) return null;
  return (
    <div
      className={"llm-nc-md chat-text" + (running ? " is-running" : "")}
      style={{ whiteSpace: "normal", fontFamily: "inherit" }}
      dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }}
    />
  );
}

function ThinkingFold({
  text,
  running,
  stateKey = "",
}: {
  text: string;
  running?: boolean;
  stateKey?: string;
}) {
  const { text: tr } = useTranslation();
  const [localOpen, setLocalOpen] = useState(false);
  const [savedOpen, setSavedOpen] = usePersistentOpen(stateKey, false);
  const open = stateKey ? savedOpen : localOpen;
  const toggle = () => {
    const next = !open;
    if (stateKey) setSavedOpen(next);
    else setLocalOpen(next);
  };
  if (!text) return null;
  const preview = text.trim().split("\n").filter(Boolean).slice(-1)[0] || text;
  return (
    <div className="llm-nc-think">
      <button
        type="button"
        className="llm-nc-think-h"
        onClick={toggle}
        aria-expanded={open}
      >
        <span>{tr("Thinking", "思考摘要")}</span>
        <span className="llm-nc-muted">
          {running ? tr("streaming…", "生成中…") : open ? "▾" : "▸"}
        </span>
      </button>
      {open ? (
        <div className="llm-nc-think-b">{text}</div>
      ) : (
        <div className="llm-nc-think-preview">{preview.slice(0, 120)}</div>
      )}
    </div>
  );
}

function DepthBoundary({ node }: { node: TNode }) {
  const { text: tr } = useTranslation();
  const openDetail = (event: React.MouseEvent) => {
    event.stopPropagation();
    const output = node.output == null
      ? node.raw_reply
      : typeof node.output === "string"
        ? node.output
        : JSON.stringify(node.output);
    useSessionStore.getState().showDetail({
      path: node.path || node.name || "nested-llm",
      name: node.name || node.node_type || "LLM",
      status: node.status || "completed",
      params: node.params,
      output,
      error: node.error,
      node_type: node.node_type,
      duration_ms: node.duration_ms,
      tree_root: node,
    });
  };
  return (
    <div className="llm-nc-muted" role="note">
      {tr("Nested content depth limit reached", "已达到嵌套内容深度限制")}
      {node.path ? (
        <button type="button" className="llm-nc-link" onClick={openDetail}>
          {tr("View in details", "在详情中查看")}
        </button>
      ) : null}
    </div>
  );
}

function ToolRefRow({
  block,
  treeRoot,
  depth = 0,
}: {
  block: StreamBlock;
  treeRoot?: TNode;
  depth?: number;
}) {
  const { text: tr } = useTranslation();
  const [localOpen, setLocalOpen] = useState(false);
  const stateKey = treeRoot?.path
    ? `${treeRoot.path}:tool:${block.block_id}` : "";
  const [savedOpen, setSavedOpen] = usePersistentOpen(stateKey, false);
  const open = stateKey ? savedOpen : localOpen;
  const child = findExecutionNode(treeRoot, block.ref_node_id)
    || findExecutionNode(treeRoot, block.tool_call_id);
  const name = block.tool_name || child?.name || block.tool_call_id || "tool";
  // A tool row without a resolved DAG child is never a completed call. This
  // also covers malformed/legacy refs that lack both ids.
  const unresolved = !child;
  const rawStatus = String(child?.status || block.status || "").toLowerCase();
  const unresolvedStatus = unresolved || rawStatus === "unknown"
    || rawStatus === "unresolved";
  const running = !unresolvedStatus && (rawStatus === "running"
    || rawStatus === "pending" || rawStatus === "queued");
  const cancelled = rawStatus === "cancelled" || rawStatus === "canceled";
  const failed = rawStatus === "error" || rawStatus === "failed"
    || rawStatus === "errored" || rawStatus === "interrupted"
    || rawStatus === "stalled" || rawStatus === "not_started"
    || !!child?.error;
  const status = unresolvedStatus
    ? "unresolved"
    : cancelled
      ? "cancelled"
      : failed
        ? "error"
        : running
          ? "running"
          : "completed";
  const err = status === "error" || status === "cancelled";
  const childChildren = child?.children || [];
  const visibleChildren = child?.expose === "io"
    ? []
    : child?.expose === "llm"
      ? childChildren.filter((c) => c.node_type === "exec" || c.name === "LLM")
      : childChildren;
  const toggle = () => {
    const next = !open;
    if (stateKey) setSavedOpen(next);
    else setLocalOpen(next);
  };

  function openDetailFor(target: TNode | undefined, e: React.MouseEvent) {
    e.stopPropagation();
    if (!target && !block.ref_node_id) return;
    const detailNode = target || child;
    const detailStatus = detailNode?.status || (target ? "completed" : status);
    const detail: DetailNode = {
      path: detailNode?.path || block.ref_node_id || block.tool_call_id || name,
      name: detailNode?.name || name,
      status: detailStatus,
      params: detailNode?.params,
      output: detailNode?.output == null
        ? detailNode?.raw_reply
        : typeof detailNode.output === "string"
          ? detailNode.output
          : JSON.stringify(detailNode.output),
      error: detailNode?.error || (!target && unresolvedStatus
        ? tr("Execution node unavailable", "执行节点暂不可用") : undefined),
      node_type: detailNode?.node_type,
      duration_ms: detailNode?.duration_ms,
      tree_root: detailNode || treeRoot,
    };
    useSessionStore.getState().showDetail(detail);
  }

  function openDetail(e: React.MouseEvent) {
    openDetailFor(child, e);
  }

  return (
    <div className={"llm-nc-tool" + (err ? " is-error" : "")}>
      <div className="llm-nc-tool-h">
        <button
          type="button"
          className="llm-nc-tool-toggle"
          onClick={toggle}
          aria-expanded={open}
        >
          <span className="llm-nc-tool-name">{open ? "▾" : "▸"} {name}</span>
        </button>
        <button type="button" className="llm-nc-link" onClick={openDetail}>
          {running
            ? tr("running…", "执行中")
            : err
              ? cancelled
                ? tr("cancelled", "已取消")
                : tr("error", "错误")
              : unresolvedStatus
                ? tr("unresolved", "未解析")
                : tr("done", "已完成")}
        </button>
      </div>
      {open ? (
        <div className="llm-nc-tool-b">
          {child?.params ? (
            <pre className="llm-nc-pre">{JSON.stringify(
              Object.fromEntries(
                Object.entries(child.params).filter(([k]) => k !== "runtime" && k !== "callback"),
              ),
              null,
              2,
            )}</pre>
          ) : (
            <div className="llm-nc-muted">{tr("Parameters on execution node", "参数见执行节点")}</div>
          )}
          {child?.error ? (
            <div className="llm-nc-err">{String(child.error)}</div>
          ) : child?.output != null ? (
            <pre className="llm-nc-pre">{typeof child.output === "string"
              ? child.output
              : JSON.stringify(child.output, null, 2)}</pre>
          ) : running ? (
            <div className="llm-nc-muted">{tr("Waiting for result…", "等待结果…")}</div>
          ) : unresolvedStatus ? (
            <div className="llm-nc-muted">
              {tr("Execution node is unavailable after recovery.", "恢复后暂时找不到执行节点。")}
            </div>
          ) : null}
          {visibleChildren.map((c, i) => (
            <div key={c.path || i} className="llm-nc-nested">
              {(c.node_type === "exec" || c.name === "LLM") ? (
                depth + 1 >= MAX_LLM_CONTENT_DEPTH ? (
                  <DepthBoundary node={c} />
                ) : (
                  <LlmNodeContent nodeId={c.path} treeRoot={c} compact depth={depth + 1} />
                )
              ) : (
                <div className="llm-nc-muted">{c.name || c.node_type}</div>
              )}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function LegacyChildren({ treeRoot, depth = 0 }: { treeRoot: TNode; depth?: number }) {
  const { text: tr } = useTranslation();
  const allChildren = treeRoot.children || [];
  const children = treeRoot.expose === "io"
    ? []
    : treeRoot.expose === "llm"
      ? allChildren.filter((child) => child.node_type === "exec" || child.name === "LLM")
      : allChildren;
  if (!children.length) return null;
  return (
    <div className="llm-nc-legacy-children" aria-label={tr(
      "Recovered execution nodes", "恢复的执行节点",
    )}>
      <div className="llm-nc-muted">
        {tr("Recovered execution nodes", "恢复的执行节点")}
      </div>
      {children.map((child, index) => {
        const isLlm = child.node_type === "exec" || child.name === "LLM";
        if (isLlm) {
          if (depth + 1 >= MAX_LLM_CONTENT_DEPTH) {
            return (
              <div key={child.path || index}>
                <DepthBoundary node={child} />
              </div>
            );
          }
          return (
            <div key={child.path || index} className="llm-nc-nested">
              <LlmNodeContent
                nodeId={child.path}
                treeRoot={child}
                fallbackText={child.raw_reply
                  || (typeof child.output === "string" ? child.output : null)}
                fallbackThinking={child.stream_reasoning || null}
                compact
                depth={depth + 1}
              />
            </div>
          );
        }
        const rawStatus = String(child.status || "").toLowerCase();
        return (
          <ToolRefRow
            key={child.path || index}
            block={{
              block_id: `legacy_tool_${child.path || index}`,
              message_id: "legacy",
              block_index: index,
              kind: "tool_ref",
              content: "",
              status: rawStatus === "running" || rawStatus === "pending"
                ? "running" : "finished",
              tool_call_id: child.path,
              ref_node_id: child.path,
              tool_name: child.name,
            }}
            treeRoot={treeRoot}
            depth={depth}
          />
        );
      })}
    </div>
  );
}

function legacyBlocksToAttempt(blocks: Array<Record<string, unknown>>): StreamAttempt | null {
  if (!blocks.length) return null;
  const normalized: StreamBlock[] = blocks.map((raw, index) => {
    const rawKind = raw.kind || raw.type;
    const kind = rawKind === "thinking" || rawKind === "reasoning_summary"
      ? "reasoning_summary"
      : rawKind === "tool" || rawKind === "tool_ref"
        ? "tool_ref"
        : rawKind === "refusal" ? "refusal"
          : typeof rawKind === "string" && rawKind
            ? rawKind
            : "text";
    return {
      block_id: String(raw.block_id || `legacy_block_${index}`),
      message_id: String(raw.message_id || "legacy"),
      block_index: Number.isFinite(Number(raw.block_index))
        ? Number(raw.block_index) : index,
      kind,
      content: kind === "tool_ref"
        ? ""
        : String(raw.text || raw.content || ""),
      status: raw.status ? String(raw.status) : "finished",
      tool_call_id: raw.tool_call_id
        ? String(raw.tool_call_id) : undefined,
      occurrence_id: raw.occurrence_id
        ? String(raw.occurrence_id) : undefined,
      ref_node_id: raw.ref_node_id || raw.node_id
        ? String(raw.ref_node_id || raw.node_id) : undefined,
      tool_name: raw.tool_name || raw.tool
        ? String(raw.tool_name || raw.tool) : undefined,
      group_id: raw.group_id ? String(raw.group_id) : undefined,
      finish_reason: raw.finish_reason
        ? String(raw.finish_reason)
        : raw.result != null ? "tool_result" : undefined,
      omitted_by_policy: raw.omitted_by_policy === true,
      truncated: raw.truncated === true,
    };
  });
  return {
    attempt_id: "legacy",
    attempt_index: 0,
    reason: "legacy",
    status: "completed",
    validation: "pending",
    blocks: normalized,
  };
}

function groupBlocks(blocks: StreamBlock[]): Array<
  | { type: "single"; block: StreamBlock }
  | { type: "parallel"; groupId: string; blocks: StreamBlock[] }
> {
  const out: Array<
    | { type: "single"; block: StreamBlock }
    | { type: "parallel"; groupId: string; blocks: StreamBlock[] }
  > = [];
  let i = 0;
  while (i < blocks.length) {
    const b = blocks[i];
    if (b.kind === "tool_ref" && b.group_id) {
      const gid = b.group_id;
      const batch: StreamBlock[] = [];
      while (i < blocks.length && blocks[i].kind === "tool_ref" && blocks[i].group_id === gid) {
        batch.push(blocks[i]);
        i += 1;
      }
      if (batch.length > 1) out.push({ type: "parallel", groupId: gid, blocks: batch });
      else out.push({ type: "single", block: batch[0] });
      continue;
    }
    out.push({ type: "single", block: b });
    i += 1;
  }
  return out;
}

function AttemptBody({
  attempt,
  treeRoot,
  nodeKey,
  depth = 0,
}: {
  attempt: StreamAttempt;
  treeRoot?: TNode;
  nodeKey?: string;
  depth?: number;
}) {
  const { text: tr } = useTranslation();
  const blocks = [...(attempt.blocks || [])].sort(
    (a, b) => (a.block_index ?? 0) - (b.block_index ?? 0),
  );
  const grouped = groupBlocks(blocks);
  if (!grouped.length) {
    return <div className="llm-nc-muted">{tr("No content yet", "暂无内容")}</div>;
  }
  return (
    <div className="llm-nc-attempt">
      {grouped.map((item, idx) => {
        if (item.type === "parallel") {
          return (
            <div key={item.groupId || idx} className="llm-nc-par">
              <div className="llm-nc-par-t">
                {tr("Parallel calls", "并行调用")} · {item.blocks.length}
              </div>
              {item.blocks.map((b) => (
                <ToolRefRow key={b.block_id} block={b} treeRoot={treeRoot} depth={depth} />
              ))}
            </div>
          );
        }
        const b = item.block;
        if (b.omitted_by_policy) {
          return (
            <div key={b.block_id} className="llm-nc-muted" role="note">
              {b.kind === "reasoning_summary"
                ? tr("Thinking not saved", "此思考内容未保存")
                : tr("Content not saved", "此内容未保存")}
            </div>
          );
        }
        if (b.kind === "reasoning_summary") {
          return (
            <ThinkingFold
              key={b.block_id}
              text={b.content || ""}
              running={b.status === "running"}
              stateKey={nodeKey ? `${nodeKey}:block:${b.block_id}` : ""}
            />
          );
        }
        if (b.kind === "text") {
          return (
            <MarkdownBody
              key={b.block_id}
              text={b.content || ""}
              running={b.status === "running"}
            />
          );
        }
        if (b.kind === "tool_ref") {
          return <ToolRefRow key={b.block_id} block={b} treeRoot={treeRoot} depth={depth} />;
        }
        if (b.kind === "refusal") {
          return (
            <div key={b.block_id} className="llm-nc-err">
              {b.content || tr("Model refused", "模型拒绝")}
            </div>
          );
        }
        return (
          <div
            key={b.block_id}
            className="llm-nc-muted llm-nc-unsupported"
            role="note"
            aria-label={tr(
              `Unsupported content block: ${b.kind}`,
              `不支持的内容块：${b.kind}`,
            )}
          >
            {tr("Unsupported content block", "不支持的内容块")} · {b.kind}
            {b.content ? ` · ${b.content}` : ""}
          </div>
        );
      })}
    </div>
  );
}

function AttemptFold({
  attempt,
  treeRoot,
  nodeKey,
  depth,
}: {
  attempt: StreamAttempt;
  treeRoot?: TNode;
  nodeKey: string;
  depth: number;
}) {
  const { text: tr } = useTranslation();
  const [open, setOpen] = usePersistentOpen(
    `${nodeKey}:attempt:${attempt.attempt_id}`,
    false,
  );
  return (
    <details
      open={open}
      className="llm-nc-old"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        {tr("Earlier attempt", "更早尝试")} #{attempt.attempt_index + 1}
        {attempt.reason ? ` · ${attempt.reason}` : ""} · {attempt.status}
      </summary>
      <AttemptBody attempt={attempt} treeRoot={treeRoot} nodeKey={nodeKey} depth={depth} />
    </details>
  );
}

export const LlmNodeContent = memo(function LlmNodeContent({
  nodeId,
  treeRoot,
  fallbackText,
  fallbackThinking,
  compact,
  depth = 0,
}: {
  nodeId?: string;
  treeRoot?: TNode;
  fallbackText?: string | null;
  fallbackThinking?: string | null;
  compact?: boolean;
  depth?: number;
}) {
  const { text: tr } = useTranslation();
  const stream = useExecutionStreamStore((s) =>
    nodeId ? s.getByNodeId(nodeId) : undefined,
  );
  const nodeKey = nodeId || treeRoot?.path || "llm";

  const persistedAttempts = useMemo(() => {
    if (treeRoot?.stream_attempts?.length) return treeRoot.stream_attempts;
    const snapshotAttempts = treeRoot?.stream_snapshot?.attempts;
    if (Array.isArray(snapshotAttempts) && snapshotAttempts.length) {
      return snapshotAttempts as StreamAttempt[];
    }
    const legacy = treeRoot?.stream_blocks;
    const attempt = legacy ? legacyBlocksToAttempt(legacy) : null;
    return attempt ? [attempt] : [];
  }, [treeRoot]);

  const attempts = stream?.attempts?.some((a) => a.blocks?.length)
    ? stream.attempts
    : persistedAttempts;

  const selected = useMemo(() => {
    if (!attempts.length) return null;
    const id = stream?.selected_attempt_id || stream?.current_attempt_id;
    return (
      attempts.find((a) => a.attempt_id === id)
      || attempts[attempts.length - 1]
      || null
    );
  }, [attempts, stream?.current_attempt_id, stream?.selected_attempt_id]);

  const older = useMemo(() => {
    if (!selected) return [];
    return attempts.filter((a) => a.attempt_id !== selected.attempt_id);
  }, [attempts, selected]);

  const selectedHasText = !!selected?.blocks.some(
    (block) => (block.kind === "text" || block.kind === "refusal")
      && !!block.content,
  );
  const selectedHasThinking = !!selected?.blocks.some(
    (block) => block.kind === "reasoning_summary" && !!block.content,
  );

  if (depth >= MAX_LLM_CONTENT_DEPTH) {
    return (
      <div className={"llm-nc" + (compact ? " is-compact" : "")}>
        <div className="llm-nc-muted" role="note">
          {tr("Nested content depth limit reached", "已达到嵌套内容深度限制")}
        </div>
      </div>
    );
  }

  if (!selected || selected.blocks.length === 0) {
    // Fallback for nodes without live stream (closed before protocol / legacy).
    return (
      <div className={"llm-nc" + (compact ? " is-compact" : "")}>
        {fallbackThinking ? <ThinkingFold text={fallbackThinking} stateKey={`${nodeKey}:fallback-thinking`} /> : null}
        {fallbackText ? <MarkdownBody text={fallbackText} /> : (
          !fallbackThinking ? (
            <div className="llm-nc-muted">{tr("No text output", "无文本输出")}</div>
          ) : null
        )}
        {treeRoot ? <LegacyChildren treeRoot={treeRoot} depth={depth} /> : null}
      </div>
    );
  }

  return (
    <div className={"llm-nc" + (compact ? " is-compact" : "")}>
      {older.map((a) => (
        <AttemptFold
          key={a.attempt_id}
          attempt={a}
          treeRoot={treeRoot}
          nodeKey={nodeKey}
          depth={depth}
        />
      ))}
      {!selectedHasThinking && fallbackThinking
        ? <ThinkingFold text={fallbackThinking} stateKey={`${nodeKey}:fallback-thinking`} /> : null}
      {!selectedHasText && fallbackText
        ? <MarkdownBody text={fallbackText} /> : null}
      <AttemptBody attempt={selected} treeRoot={treeRoot} nodeKey={nodeKey} depth={depth} />
    </div>
  );
});
