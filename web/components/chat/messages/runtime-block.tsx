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

/** Extract the function name from a ``run fn(args)`` / ``run fn arg1``
 *  command — RuntimeBlock no longer renders the params in its header,
 *  so we don't need to parse them out. */
function parseRun(cmd: string): { fn: string } {
  const text = cmd.replace(/^(run|create|fix)\s+/i, "").trim();
  const paren = text.match(/^([\w.-]+)\s*\(/);
  if (paren) return { fn: paren[1] };
  const sp = text.indexOf(" ");
  return { fn: sp < 0 ? text : text.slice(0, sp) };
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

export function RuntimeBlock({
  msg,
  nested,
}: {
  msg: ChatMsg;
  /** True when rendered inside an assistant bubble (i.e. the call was
   *  initiated by the LLM itself, not by the user via fn-form). The
   *  user can't usefully "retry" a call the model made on its own —
   *  hide the Retry button in that mode, keep Copy JSON. */
  nested?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const { text } = useTranslation();
  useMarkdownReady();

  const sessionId = useSessionStore((s) => s.currentSessionId);
  const streaming =
    msg.status === "streaming" ||
    msg.status === "pending" ||
    msg.status === "running";
  const { fn } = parseRun(msg.function || msg.content || "");
  const fnName = msg.function || fn;
  const tree = displayTree(msg);

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

  // Static header label — the function name + args are already
  // visible on the body's root tree row, so repeating them up here
  // both wastes space and forces a choice ("which call gets the
  // title?") that has no good answer when the tree contains many
  // nested calls. Drop the signature; keep the frame.
  const headerLabel = text("Function call", "函数调用");

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
      {!streaming && fnName && !nested ? (
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
            <span className="indicator-dot pulse-opacity" />{" "}
            {headerLabel}
          </span>
          <span className="inline-tree-actions">{actions}</span>
        </div>
        <div className="inline-tree-body">
          <div className="pending-body" style={{ padding: "4px 0" }}>
            <span className="indicator-dot pulse-scale" aria-hidden="true" />
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
