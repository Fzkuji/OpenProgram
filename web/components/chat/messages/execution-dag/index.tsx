"use client";

/**
 * Execution tree — React port of the legacy `renderInlineTree` /
 * `renderTreeNode` (`public/js/chat/tree-render.js`) plus the per-node
 * retry panel (`tree-retry.js`).
 *
 * Renders the `/run` execution DAG inside a runtime block: a
 * collapsible card whose body is a recursive node list. Each node row
 * can expand/collapse its children, be selected (opens the right-rail
 * Execution Detail via the legacy `window.showDetail`), and — for
 * non-LLM, non-running nodes — open a "modify" panel that re-runs the
 * node with edited params (`retry_node` WS action).
 *
 * State that was global in the vanilla version is now component-local:
 * `expanded` replaces `expandedNodes`, selection replaces
 * `selectedPath`, and the node objects are walked directly instead of
 * via the `_nodeCache` path map.
 */
import { useCallback, useEffect, useState } from "react";

import { useSessionStore } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

import {
  cleanForCopy,
  collectPaths,
  type TNode,
  treeHasRunning,
  truncate,
} from "./types";
import { RetryPanel } from "./retry-panel";


/** Backend's `_to_tnode` historically called `json.dumps()` with the
 *  default `ensure_ascii=True`, producing strings like
 *  `'{"task": "\\u4f60..."}'` — so old persisted trees show raw
 *  `\uXXXX` escapes instead of Chinese. New trees ship the dict
 *  natively (or with `ensure_ascii=False`), so this helper is a
 *  read-side compat for those old payloads.
 *
 *  Strategy: if the value is already an object, restringify it (JS
 *  default doesn't escape non-ASCII). If it's a string that parses
 *  as JSON, parse + restringify. Otherwise — last-resort regex sweep
 *  to decode bare `\uXXXX` sequences embedded in plain text. */
function decodeUnicodeEscapes(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "object") return JSON.stringify(v);
  if (typeof v !== "string") return String(v);
  if (!v.includes("\\u")) return v;
  try {
    const parsed = JSON.parse(v);
    if (typeof parsed === "object" && parsed !== null) return JSON.stringify(parsed);
    if (typeof parsed === "string") return parsed;
  } catch {
    /* not parseable JSON — fall through to regex sweep */
  }
  return v.replace(/\\u([0-9a-fA-F]{4})/g, (_m, hex) =>
    String.fromCharCode(parseInt(hex, 16)),
  );
}


/* ---- node row ------------------------------------------------------ */

interface RowCtx {
  expanded: Set<string>;
  toggle: (path: string) => void;
  selectedPath: string | null;
  select: (node: TNode) => void;
  retryOpen: Set<string>;
  toggleRetry: (path: string) => void;
  paused: boolean;
  text: (en: string, zh: string) => string;
  /** Re-render tick — bumped every second so running durations advance. */
  tick: number;
}

function TreeNodeRow({ node, ctx }: { node: TNode; ctx: RowCtx }) {
  const path = node.path ?? "";
  const hasChildren = !!node.children && node.children.length > 0;
  const isExpanded = ctx.expanded.has(path);
  const isSelected = path === ctx.selectedPath;

  const hasFinished =
    (!!node.duration_ms && node.duration_ms > 0) ||
    (!!node.end_time && node.end_time > 0);
  const effectiveStatus =
    node.status === "running" && hasFinished ? "error" : node.status;
  const isCancelled =
    effectiveStatus === "error" &&
    typeof node.error === "string" &&
    /cancel/i.test(node.error);
  const displayStatus =
    ctx.paused && effectiveStatus === "running" ? "paused" : effectiveStatus;

  const icon =
    displayStatus === "success" ? (
      <span style={{ color: "var(--accent-green)" }}>{"✓"}</span>
    ) : isCancelled ? (
      <span style={{ color: "var(--text-muted)" }} title={ctx.text("Cancelled", "已取消")}>
        {"◉"}
      </span>
    ) : displayStatus === "error" ? (
      <span style={{ color: "var(--accent-red)" }}>{"✗"}</span>
    ) : displayStatus === "paused" ? (
      <span style={{ color: "var(--accent-yellow)" }}>{"❙❙"}</span>
    ) : (
      <span className="pulse" style={{ color: "var(--accent-blue)" }}>
        {"●"}
      </span>
    );

  let dur = "";
  const running = displayStatus === "running" || displayStatus === "paused";
  if (node.duration_ms && node.duration_ms > 0) {
    dur =
      node.duration_ms >= 1000
        ? (node.duration_ms / 1000).toFixed(1) + "s"
        : Math.round(node.duration_ms) + "ms";
  } else if (running && node.start_time && node.start_time > 0) {
    const elapsed = Math.round(Date.now() / 1000 - node.start_time);
    dur = displayStatus === "paused" ? elapsed + ctx.text("s (paused)", "秒（已暂停）") : elapsed + "s...";
  }

  const isExec = node.node_type === "exec";
  let preview = "";
  let output = "";
  if (isExec) {
    const execIn =
      (node.params && (node.params._content as string)) || "";
    const execOut =
      node.raw_reply ||
      (typeof node.output === "string" ? node.output : "");
    const inPart = execIn ? "→ " + truncate(execIn, 50) : "";
    const outPart = execOut ? " ← " + truncate(execOut, 50) : "";
    preview = (inPart + outPart).trim();
  } else if (node.output != null) {
    output = truncate(decodeUnicodeEscapes(node.output), 80);
  }

  const canRetry =
    !isExec && node.name !== "chat_session" && node.status !== "running";

  return (
    <div className="tree-node">
      <div
        className={
          "node-row" +
          (isSelected ? " selected" : "") +
          (isExec ? " exec-row" : "")
        }
        onClick={() => ctx.select(node)}
      >
        <span
          className={
            "node-toggle " +
            (hasChildren ? (isExpanded ? "expanded" : "") : "leaf")
          }
          onClick={(e) => {
            e.stopPropagation();
            if (hasChildren) ctx.toggle(path);
          }}
        >
          {"▶"}
        </span>
        <span className="node-icon">{icon}</span>
        {isExec ? (
          <span className="llm-badge" title="LLM call">
            LLM
          </span>
        ) : (
          <span
            className="node-name"
            style={{ cursor: "pointer" }}
            title={ctx.text("View source", "查看源码")}
            onClick={(e) => {
              e.stopPropagation();
              (
                window as unknown as { viewSource?: (n: string) => void }
              ).viewSource?.(node.name ?? "");
            }}
          >
            {node.name}
          </span>
        )}
        {!isExec && (
          <span
            className={
              "node-status " +
              displayStatus +
              (isCancelled ? " cancelled" : "")
            }
          >
            {isCancelled ? ctx.text("cancelled", "已取消") : ctx.text(displayStatus, statusZh(displayStatus))}
          </span>
        )}
        {dur ? <span className="node-duration">{dur}</span> : null}
        {preview ? (
          <span className="node-output-preview exec-preview">{preview}</span>
        ) : null}
        {output ? (
          <span className="node-output-preview">{output}</span>
        ) : null}
        {canRetry ? (
          <span
            className="retry-icon"
            title={ctx.text("Modify", "修改")}
            onClick={(e) => {
              e.stopPropagation();
              ctx.toggleRetry(path);
            }}
          >
            modify
          </span>
        ) : null}
      </div>

      {canRetry && ctx.retryOpen.has(path) ? (
        <RetryPanel node={node} onClose={() => ctx.toggleRetry(path)} />
      ) : null}

      {hasChildren ? (
        <div className={"node-children" + (isExpanded ? "" : " collapsed")}>
          {node.children!.map((child, i) => (
            <TreeNodeRow key={child.path ?? i} node={child} ctx={ctx} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

/* ---- tree card ----------------------------------------------------- */

interface ExecutionDagProps {
  tree: TNode;
  /** Override the default "Execution DAG" / "执行 DAG" header label.
   *  Used by ``RuntimeBlock`` to surface the function signature directly
   *  in the inline-tree header so any function call (agentic or regular
   *  tool) renders with a unified `.inline-tree` frame. */
  headerLabel?: React.ReactNode;
  /** Extra elements rendered inside ``inline-tree-actions`` before the
   *  Copy JSON button — e.g. attempt-nav arrows, Retry. */
  actions?: React.ReactNode;
  /** Forwarded to the wrapper so legacy CLI/stream code can target the
   *  pending block via ``id="runtime_pending"`` / ``data-function``. */
  pendingId?: string;
  dataFunction?: string;
}

export function ExecutionDag({
  tree,
  headerLabel,
  actions,
  pendingId,
  dataFunction,
}: ExecutionDagProps) {
  const paused = useSessionStore((s) => s.paused);
  const { text } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    const s = new Set<string>();
    collectPaths(tree, s);
    return s;
  });
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [retryOpen, setRetryOpen] = useState<Set<string>>(new Set());
  const [copied, setCopied] = useState(false);

  // Running nodes show a live "12s..." duration — re-render every
  // second while the tree still has one.
  const running = treeHasRunning(tree);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [running]);

  const toggle = useCallback((path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const toggleRetry = useCallback((path: string) => {
    setRetryOpen((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const select = useCallback((node: TNode) => {
    setSelectedPath(node.path ?? null);
    (
      window as unknown as { showDetail?: (n: unknown) => void }
    ).showDetail?.(node);
  }, []);

  function copy() {
    const json = JSON.stringify(cleanForCopy(tree), null, 2);
    const done = () => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(json).then(done, done);
    } else {
      done();
    }
  }

  const ctx: RowCtx = {
    expanded,
    toggle,
    selectedPath,
    select,
    retryOpen,
    toggleRetry,
    paused,
    text,
    tick,
  };

  return (
    <div
      className="inline-tree"
      id={pendingId}
      data-function={dataFunction || undefined}
    >
      <div
        className="inline-tree-header"
        onClick={() => setCollapsed((c) => !c)}
      >
        <span>
          {running ? (
            <span className="pulse" style={{ color: "var(--accent-blue)" }}>
              {"●"}
            </span>
          ) : (
            <span className="inline-tree-script" title="function">{"𝓕"}</span>
          )}
          {"\u00a0\u00a0"}
          {headerLabel ?? text("Execution DAG", "执行 DAG")}
        </span>
        <span className="inline-tree-actions">
          {actions}
          <button
            className={"inline-tree-copy" + (copied ? " copied" : "")}
            title={text("Copy tree as JSON", "复制执行树 JSON")}
            onClick={(e) => {
              e.stopPropagation();
              copy();
            }}
          >
            {copied ? text("Copied", "已复制") : text("Copy", "复制")}
          </button>
          <span
            className={"inline-tree-toggle" + (collapsed ? " collapsed" : "")}
          >
            {"▶"}
          </span>
        </span>
      </div>
      <div className={"inline-tree-body" + (collapsed ? " collapsed" : "")}>
        <TreeNodeRow node={tree} ctx={ctx} />
      </div>
    </div>
  );
}

function statusZh(status: string): string {
  if (status === "success") return "成功";
  if (status === "error") return "错误";
  if (status === "paused") return "已暂停";
  if (status === "running") return "运行中";
  if (status === "pending") return "等待中";
  return status;
}
