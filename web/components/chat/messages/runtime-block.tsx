"use client";

/**
 * Function-call block — renders ANY function call (agentic
 * @agentic_function OR a regular LLM tool call surfaced via /run-style
 * runtime envelope) as a unified ``.inline-tree`` card, the same shape
 * used by ``ToolsBlock`` for regular tool calls. No outer
 * ``runtime-block`` frame, no separate "return:" preview — the
 * execution tree IS the visualisation.
 *
 * Header label = ``fnName(params)`` so the user immediately sees what
 * was called with what kwargs. Retry / attempt-nav move into the
 * ``inline-tree-actions`` slot so the card has one frame, not two.
 */
import { useEffect, useRef } from "react";

import { formatUsageFooterLabel } from "@/lib/format";
import { useSessionStore, type ChatMsg } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

import { ExecutionDag } from "./execution-dag/index";
import { useMarkdownReady } from "./markdown";

interface RuntimeLegacyGlobals {
  retryCurrentBlock?: (fn: string) => void;
  renderMathInElement?: (el: HTMLElement, opts: unknown) => void;
}

function wsSend(payload: unknown): boolean {
  const w = window as Window & { ws?: WebSocket };
  if (!w.ws || w.ws.readyState !== WebSocket.OPEN) return false;
  w.ws.send(JSON.stringify(payload));
  return true;
}

/** Split a `run fn(args)` / `run fn arg1 arg2` command into name +
 *  params for the header signature. */
function parseRun(cmd: string): { fn: string; params: string } {
  const text = cmd.replace(/^(run|create|fix)\s+/i, "").trim();
  const paren = text.match(/^([\w.-]+)\s*\(([^]*)\)\s*$/);
  if (paren) return { fn: paren[1], params: paren[2] };
  const sp = text.indexOf(" ");
  if (sp < 0) return { fn: text, params: "" };
  return { fn: text.slice(0, sp), params: text.slice(sp + 1).trim() };
}

/** Tree for the selected attempt — mirrors legacy `_getDisplayContent`,
 *  minus the rawContent we no longer surface (it's redundant with the
 *  execution tree's terminal LLM node). */
function displayTree(msg: ChatMsg): unknown {
  let tree: unknown = msg.contextTree || null;
  if (msg.attempts && msg.attempts.length > 0) {
    const att = msg.attempts[msg.current_attempt || 0];
    if (att && att.tree) tree = att.tree;
  }
  return tree;
}

/** Format the call-site kwargs for the header so the user sees what
 *  was passed in, not just an empty ``()``. Pulls from the Execution
 *  DAG root's ``params`` field (the wrapper records kwargs there).
 *  Truncates long string values. */
function formatHeaderParams(tree: unknown): string {
  if (!tree || typeof tree !== "object") return "";
  const t = tree as { params?: Record<string, unknown> };
  const p = t.params;
  if (!p || typeof p !== "object") return "";
  const parts: string[] = [];
  for (const [k, v] of Object.entries(p)) {
    if (k === "runtime" || k === "callback") continue;
    let s: string;
    if (typeof v === "string") {
      s = v.length > 40 ? `"${v.slice(0, 40)}…"` : `"${v}"`;
    } else if (v === null || v === undefined) {
      s = "null";
    } else if (typeof v === "object") {
      s = "{...}";
    } else {
      s = String(v);
    }
    parts.push(`${k}: ${s}`);
  }
  return parts.join(", ");
}

export function RuntimeBlock({ msg }: { msg: ChatMsg }) {
  const ref = useRef<HTMLDivElement>(null);
  const { text } = useTranslation();
  useMarkdownReady();

  const sessionId = useSessionStore((s) => s.currentSessionId);
  const streaming =
    msg.status === "streaming" ||
    msg.status === "pending" ||
    msg.status === "running";
  const { fn, params: parsedParams } = parseRun(msg.function || msg.content || "");
  const fnName = msg.function || fn;
  const tree = displayTree(msg);
  const params = formatHeaderParams(tree) || parsedParams;

  useEffect(() => {
    const el = ref.current;
    const renderMath = (window as unknown as RuntimeLegacyGlobals)
      .renderMathInElement;
    if (el && renderMath) {
      try {
        renderMath(el, {
          delimiters: [
            { left: "$$", right: "$$", display: true },
            { left: "$", right: "$", display: false },
          ],
        });
      } catch {
        /* ignore */
      }
    }
  }, [tree]);

  const w = window as unknown as RuntimeLegacyGlobals;
  const attempts = msg.attempts ?? [];
  const attemptIdx = msg.current_attempt || 0;
  const hasAttempts = attempts.length > 1;
  const usageHtml = !streaming
    ? formatUsageFooterLabel(
        (msg.usage as Parameters<typeof formatUsageFooterLabel>[0]) || null,
      )
    : "";

  function switchAttempt(dir: number) {
    const next = attemptIdx + dir;
    if (next < 0 || next >= attempts.length || !sessionId) return;
    wsSend({
      action: "switch_attempt",
      session_id: sessionId,
      function: fnName,
      attempt_index: next,
    });
  }

  const headerLabel = (
    <>
      <span className="runtime-func">{fnName}</span>
      {params ? (
        <>
          (<span className="runtime-params">{params}</span>)
        </>
      ) : (
        "()"
      )}
    </>
  );

  const actions = (
    <>
      {hasAttempts ? (
        <span className="attempt-nav" onClick={(e) => e.stopPropagation()}>
          <button
            className="attempt-nav-btn"
            disabled={attemptIdx <= 0}
            title={text("Previous attempt", "上一次尝试")}
            onClick={() => switchAttempt(-1)}
          >
            {"◀"}
          </button>
          <span className="attempt-nav-label">
            {attemptIdx + 1}/{attempts.length}
          </span>
          <button
            className="attempt-nav-btn"
            disabled={attemptIdx >= attempts.length - 1}
            title={text("Next attempt", "下一次尝试")}
            onClick={() => switchAttempt(1)}
          >
            {"▶"}
          </button>
        </span>
      ) : null}
      {!streaming && fnName ? (
        <button
          className="rerun-btn"
          title={text("Retry", "重试")}
          onClick={(e) => {
            e.stopPropagation();
            w.retryCurrentBlock?.(fnName);
          }}
        >
          {text("↻ Retry", "↻ 重试")}
        </button>
      ) : null}
    </>
  );

  // No tree yet (just-spawned placeholder) — render a tiny inline-tree
  // shell so the user sees the function frame immediately. As soon as
  // the first tree_update lands, ExecutionDag takes over rendering.
  if (!tree) {
    return (
      <div
        ref={ref}
        className="inline-tree"
        id={streaming ? "runtime_pending" : undefined}
        data-function={fnName || undefined}
        data-msg-id={msg.id}
      >
        <div className="inline-tree-header">
          <span>
            <span className="pulse" style={{ color: "var(--accent-blue)" }}>
              {"●"}
            </span>{" "}
            {headerLabel}
          </span>
          <span className="inline-tree-actions">{actions}</span>
        </div>
        <div className="inline-tree-body">
          <div className="pending-body" style={{ padding: "4px 0" }}>
            <span className="pending-pulse" aria-hidden="true" />
            <span className="pending-label">
              {text("Running…", "运行中…")}
            </span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div ref={ref} data-msg-id={msg.id}>
      <ExecutionDag
        tree={tree as never}
        headerLabel={headerLabel}
        actions={actions}
        pendingId={streaming ? "runtime_pending" : undefined}
        dataFunction={fnName || undefined}
      />
      {usageHtml ? (
        <div
          className="runtime-usage-footer"
          dangerouslySetInnerHTML={{ __html: usageHtml }}
        />
      ) : null}
    </div>
  );
}
