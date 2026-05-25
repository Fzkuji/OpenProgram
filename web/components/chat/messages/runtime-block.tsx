"use client";

/**
 * Runtime block — a `/run <fn>` turn.
 *
 * Real React now (no more delegation to the legacy `buildRuntimeBlockHtml`):
 * a collapsible header, the `return:` output, the React <ExecutionDag />,
 * and a footer with Retry / attempt-nav / usage. While the turn is still
 * streaming the block renders a pending placeholder carrying
 * `id="runtime_pending"` so the legacy CLI/tree stream handlers can
 * target it; on finalize React takes over with the full block.
 *
 * Retry (`retryCurrentBlock`) is still a legacy global — it belongs to
 * the conversation / WS layer, migrated in a later slice.
 */
import { useEffect, useRef, useState } from "react";

import { formatUsageFooterLabel } from "@/lib/format";
import { useSessionStore, type ChatMsg } from "@/lib/session-store";

import { ExecutionDag } from "./execution-dag/index";
import { renderMarkdown, useMarkdownReady } from "./markdown";

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

/** Content + tree for the selected attempt — mirrors legacy
 *  `_getDisplayContent`. */
function displayContent(msg: ChatMsg): { content: string; tree: unknown } {
  let content = msg.content || "";
  let tree: unknown = msg.contextTree || null;
  if (msg.attempts && msg.attempts.length > 0) {
    const att = msg.attempts[msg.current_attempt || 0];
    if (att) {
      content = att.content || content;
      tree = att.tree || tree;
    }
  }
  return { content, tree };
}

export function RuntimeBlock({ msg }: { msg: ChatMsg }) {
  const ref = useRef<HTMLDivElement>(null);
  const [collapsed, setCollapsed] = useState(false);
  useMarkdownReady();

  const sessionId = useSessionStore((s) => s.currentSessionId);
  const streaming = msg.status === "streaming" || msg.status === "pending";
  const { fn, params } = parseRun(msg.function || msg.content || "");
  const fnName = msg.function || fn;
  const { content, tree } = displayContent(msg);

  // KaTeX pass over the rendered output (same as the legacy renderer).
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
  }, [content]);

  const cls = [
    "runtime-block",
    collapsed ? "collapsed" : "",
    streaming ? "runtime-block-pending" : "",
    msg.status === "error" ? "error" : "",
  ]
    .filter(Boolean)
    .join(" ");

  const header = (
    <div
      className="runtime-block-header"
      onClick={() => setCollapsed((c) => !c)}
    >
      <span className="runtime-icon">{"▶"}</span>
      <span className="runtime-func">
        {fnName}
        {params ? (
          <>
            (<span className="runtime-params">{params}</span>)
          </>
        ) : (
          "()"
        )}
      </span>
      {!streaming ? (
        <span className="runtime-result-preview">
          {"-> " +
            content.replace(/\s+/g, " ").trim().slice(0, 60) +
            (content.length > 60 ? "…" : "")}
        </span>
      ) : null}
    </div>
  );

  if (streaming) {
    return (
      <div ref={ref} className={cls} id="runtime_pending" data-function={fnName}>
        {header}
        <div className="runtime-block-body">
          <div className="runtime-block-content">
            {tree ? (
              <ExecutionDag tree={tree as never} />
            ) : (
              <div className="typing-indicator">
                <div className="dot" />
                <div className="dot" />
                <div className="dot" />
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  const w = window as unknown as RuntimeLegacyGlobals;
  const attempts = msg.attempts ?? [];
  const attemptIdx = msg.current_attempt || 0;

  // Switch the displayed attempt — sends `switch_attempt`; the server
  // moves the pointer and a `load_session` re-feed re-renders.
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
  const usageHtml = formatUsageFooterLabel(
    (msg.usage as Parameters<typeof formatUsageFooterLabel>[0]) || null,
  );
  const hasFooter = !!fnName || attempts.length > 1 || !!usageHtml;

  return (
    <div
      ref={ref}
      className={cls}
      data-function={fnName || undefined}
      data-msg-id={msg.id}
    >
      {header}
      <div className="runtime-block-body">
        <div className="runtime-block-content">
          <div className="runtime-result">
            <span className="runtime-return-label">return:</span>
          </div>
          <div
            className="runtime-output"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }}
          />
          {tree ? <ExecutionDag tree={tree as never} /> : null}
        </div>
      </div>
      {hasFooter ? (
        <div className="runtime-block-footer">
          <div className="runtime-footer-left">
            {fnName ? (
              <button
                className="rerun-btn"
                onClick={() => w.retryCurrentBlock?.(fnName)}
              >
                {"↻ Retry"}
              </button>
            ) : null}
          </div>
          <div className="runtime-footer-center">
            {attempts.length > 1 ? (
              <div className="attempt-nav">
                <button
                  className="attempt-nav-btn"
                  disabled={attemptIdx <= 0}
                  title="Previous attempt"
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
                  title="Next attempt"
                  onClick={() => switchAttempt(1)}
                >
                  {"▶"}
                </button>
              </div>
            ) : null}
          </div>
          <div
            className="runtime-footer-right"
            dangerouslySetInnerHTML={{ __html: usageHtml }}
          />
        </div>
      ) : null}
    </div>
  );
}
