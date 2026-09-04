"use client";

/**
 * Manual function run — renders a fn-form///run function call as the
 * same frameless execution timeline chat turns use
 * (docs/design/ui/chat-turn-visual-spec.html §3): TreeStep root with
 * only its direct children open by default, plus a
 * message-style footer below the tree — timestamp + Copy(result) /
 * Retry / Edit + the shared ``.message-nav`` version switcher.
 *
 * Fork semantics: Retry = sibling branch with unchanged params; Edit
 * reopens the fn-form prefilled and submits as a sibling branch with
 * edited params (fork_of_node). Old runs stay reachable via ◀ N/M ▶ —
 * nothing is ever appended at the conversation tail. Nested renders
 * (inside an assistant bubble) drop the footer.
 */
import { useEffect, useRef, useState } from "react";

import { formatUsageFooterLabel } from "@/lib/format-utils/format";
import { renderMathIn } from "@/lib/format-utils/markdown";
import { getSocket, runtimeState } from "@/lib/runtime-bridge/state";
import {
  useSessionStore,
  type AgenticFunction,
  type ChatMsg,
} from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { showToast } from "@/lib/format-utils/toast";
import { optimisticAction } from "@/lib/runtime-bridge/optimistic-action";

import type { TNode } from "./tree-types";
import { ExecutionStrip, StepRow, TreeStep, decodeEscapes } from "./execution-strip";
import { ActionButton, MessageTimestamp, SVG } from "./message-actions";
import { renderMarkdown, useMarkdownReady } from "./markdown";
import { runtimeAnswer, runtimeConclusion, runtimeSummaryLabel } from "./runtime-summary";

function wsSend(payload: unknown): boolean {
  const sock = getSocket();
  if (!sock || sock.readyState !== WebSocket.OPEN) return false;
  sock.send(JSON.stringify(payload));
  return true;
}

/** Move HEAD to a sibling version, then reload so the transcript shows
 *  only that branch's run. Same op the chat-message ``< N/M >`` nav uses
 *  (POST /api/chat/checkout) — a pure display switch, nothing re-runs.
 *
 *  Optimistic (interaction-feedback policy): flip the CURRENT card into a
 *  spinner body + the target sibling index at 0ms so the click registers
 *  instantly. The checkout POST + ``load_session`` replaces the transcript
 *  (~1 round-trip) with the target branch's real run; that reload wipes this
 *  card's id from the store, which is our "settled" signal. On timeout we
 *  restore the pre-click card and toast. */
function checkoutSibling(
  sessionId: string,
  targetId: string,
  currentMsg: ChatMsg,
  targetIndex: number,
): void {
  const store = useSessionStore.getState();
  const id = currentMsg.id;
  const snapshot = store.messagesById[id];
  optimisticAction(
    {
      apply: () => {
        store.updateMessage(sessionId, id, {
          status: "running",
          contextTree: undefined,
          siblingIndex: targetIndex,
        });
      },
      // The load_session reload rebuilds the transcript with new ids, so
      // this card's id is gone from the store once the switch lands.
      settled: () => !useSessionStore.getState().messagesById[id],
      revert: () => {
        if (snapshot) useSessionStore.getState().updateMessage(sessionId, id, snapshot);
      },
      onTimeoutMessage: "Version switch timed out — reverted.",
    },
    showToast,
  );
  fetch("/api/chat/checkout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, msg_id: targetId }),
  })
    .then(() => wsSend({ action: "load_session", session_id: sessionId }))
    .catch(() => {
      /* revert is handled by the optimisticAction timeout */
    });
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

/** Tree for this run. Each Retry is a separate sibling node with its
 *  OWN contextTree (only the active branch's node renders), so we read
 *  the node's tree directly — no per-message attempts array anymore. */
function displayTree(msg: ChatMsg): TNode | null {
  return (msg.contextTree as TNode | undefined) || null;
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
  const [copied, setCopied] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useMarkdownReady();

  const sessionId = useSessionStore((s) => s.currentSessionId);
  const streaming =
    msg.status === "streaming" ||
    msg.status === "pending" ||
    msg.status === "running" ||
    msg.status === "cancelling";
  const { fn } = parseRun(msg.function || msg.content || "");
  const fnName = msg.function || fn;
  const tree = displayTree(msg);

  useEffect(() => {
    if (nested || !streaming) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [nested, streaming]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    try {
      renderMathIn(el);
    } catch {
      /* ignore */
    }
  }, [tree]);

  // Version navigation: a Retry forks the call as a SIBLING branch, so
  // the runs are DAG siblings (same predecessor) — navigated with the
  // same < N/M > switcher chat messages use, via HEAD checkout. Only the
  // active sibling is on the current branch, so the transcript renders
  // exactly one run; the switcher (and the Branches panel) reach the rest.
  const siblingIdx = msg.siblingIndex ?? 0; // 1-based
  const siblingTotal = msg.siblingTotal ?? 0;
  const hasSiblings = siblingTotal > 1;
  const usageHtml = !streaming
    ? formatUsageFooterLabel(
        (msg.usage as Parameters<typeof formatUsageFooterLabel>[0]) || null,
      )
    : "";

  // Fallback for malformed legacy runs without a stored function name.
  const headerLabel = text("Function call", "函数调用");
  const summaryLabel = runtimeSummaryLabel({
    fnName: fnName || headerLabel,
    status: msg.status,
    timestamp: msg.timestamp,
    now,
    tree,
    text,
  });
  const conclusion = runtimeConclusion({
    fnName: fnName || headerLabel,
    status: msg.status,
    tree,
    text,
  });
  const answer = nested ? null : runtimeAnswer({ fnName, status: msg.status, tree });

  // Re-run the SAME function with its LAST kwargs in the SAME session.
  // The backend looks up the prior call's stored args and dispatches via
  // the forced-tool-call path (fresh sibling run, not an overwrite).
  function doRetry() {
    if (!sessionId) return;
    // 0ms feedback (interaction-feedback policy): flip THIS card into the
    // new-version pending state right now; the reload on
    // running_task_clear backfills the real run. Stuck retry reverts.
    const store = useSessionStore.getState();
    const rid = msg.id;
    const snapshot = store.messagesById[rid];
    const total = (msg.siblingTotal ?? 0) + 1;
    optimisticAction(
      {
        apply: () => {
          store.updateMessage(sessionId, rid, {
            status: "running",
            contextTree: undefined,
            siblingIndex: total,
            siblingTotal: total,
          });
        },
        settled: () => !useSessionStore.getState().messagesById[rid],
        revert: () => {
          if (snapshot) {
            useSessionStore.getState().updateMessage(sessionId, rid, snapshot);
          }
        },
        onTimeoutMessage: text(
          "Retry timed out — reverted.",
          "重试超时——已还原。",
        ),
      },
      showToast,
    );
    runtimeState.__reloadOnTaskClear.add(sessionId);
    wsSend({ action: "retry_function", session_id: sessionId, function: fnName });
  }

  // 复制 = 根调用的返回值（用户关心的是结果，不是内部树结构）；
  // 还没有输出时退回 "函数名 + 入参"。
  function copyResult() {
    const root = tree;
    const out = root?.error ?? root?.output;
    const payload = out !== undefined && out !== null && String(out).trim() !== ""
      ? decodeEscapes(String(out))
      : JSON.stringify({ function: fnName, params: root?.params }, null, 2);
    const done = () => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(payload).then(done, done);
    } else done();
  }

  // 修改：重新弹出 fn-form，预填上次的参数；提交时以本次运行为锚点
  // fork 兄弟分支（旧运行保留在 ◀ N/M ▶ 里），语义 = 可改参数的重试。
  function editCall() {
    const root = tree;
    const fn = (runtimeState.availableFunctions as AgenticFunction[]).find(
      (f) => f.name === fnName,
    );
    if (!fn) {
      showToast(text(
        `Function ${fnName} not found.`, `找不到函数 ${fnName}。`,
      ), { tone: "error" });
      return;
    }
    const prefill: Record<string, string> = {};
    for (const [k, v] of Object.entries(root?.params || {})) {
      if (k === "runtime" || k === "callback") continue;
      prefill[k] = typeof v === "boolean"
        ? (v ? "True" : "False")
        : typeof v === "string" ? v : JSON.stringify(v);
    }
    useSessionStore.getState().openFnFormEdit(fn, prefill, msg.id);
  }

  // 底部行始终提供开始时间；顶层手动运行另外提供
  // 复制/重试/修改和版本切换，嵌套调用不暴露这些操作。
  const footer = (
    <div className="message-actions-footer runtime-actions-footer">
      <div className="message-actions">
        <MessageTimestamp timestamp={msg.timestamp} />
        {!nested ? (
          <ActionButton
            icon={copied ? SVG.check : SVG.copy}
            title={text("Copy result", "复制结果")}
            extraClass={copied ? "is-copied" : undefined}
            onClick={copyResult}
          />
        ) : null}
        {!nested && !streaming && fnName ? (
          <>
            <ActionButton
              icon={SVG.retry}
              title={text("Retry", "重试")}
              onClick={doRetry}
            />
            <ActionButton
              icon={SVG.pencil}
              title={text("Edit and re-run", "修改后重新运行")}
              onClick={editCall}
            />
          </>
        ) : null}
        {!nested && hasSiblings ? (
          <div className="message-nav">
            <button
              type="button"
              className="message-nav-btn"
              data-nav="prev"
              aria-label={text("Previous version", "上一个版本")}
              disabled={siblingIdx <= 1 || !sessionId || !msg.prevSiblingId}
              onClick={() =>
                sessionId &&
                msg.prevSiblingId &&
                checkoutSibling(sessionId, msg.prevSiblingId, msg, siblingIdx - 1)
              }
            >
              {SVG.chevL}
            </button>
            <span className="message-nav-label">
              {siblingIdx} / {siblingTotal}
            </span>
            <button
              type="button"
              className="message-nav-btn"
              data-nav="next"
              aria-label={text("Next version", "下一个版本")}
              disabled={
                siblingIdx >= siblingTotal || !sessionId || !msg.nextSiblingId
              }
              onClick={() =>
                sessionId &&
                msg.nextSiblingId &&
                checkoutSibling(sessionId, msg.nextSiblingId, msg, siblingIdx + 1)
              }
            >
              {SVG.chevR}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );

  const body = tree ? (
    <TreeStep node={tree} defaultKidsOpen />
  ) : (
    <StepRow
      icon="function"
      title={fnName || headerLabel}
      note={text("Running…", "运行中…")}
      running
    />
  );
  const runtimeAfter = (
    usageHtml ? (
      <div
        className="runtime-usage-footer"
        dangerouslySetInnerHTML={{ __html: usageHtml }}
      />
    ) : null
  );

  // LLM-initiated calls keep the existing frameless tree unchanged.
  if (nested) {
    return (
      <div
        ref={ref}
        className="tl"
        data-open="1"
        id={streaming ? "runtime_pending" : undefined}
        data-function={fnName || undefined}
        data-msg-id={msg.id}
      >
        <div className="tl-body">{body}</div>
        {runtimeAfter}
        {footer}
      </div>
    );
  }

  // The function marker identifies the runtime row's type; it does not turn
  // the workflow into an assistant/agent message identity.
  return (
    <div
      ref={ref}
      className="runtime-program-run"
      id={streaming ? "runtime_pending" : undefined}
      data-function={fnName || undefined}
      data-msg-id={msg.id}
      role="article"
      aria-label={`${text("Workflow", "工作流")} ${fnName || headerLabel}`}
    >
      <div className="runtime-program-avatar" aria-hidden="true">ƒ</div>
      <div className="runtime-program-content">
        <ExecutionStrip
          label={summaryLabel}
          streaming={streaming}
          after={runtimeAfter}
        >
          {body}
        </ExecutionStrip>
        {answer ? (
          <section className="runtime-program-conclusion" aria-label={text("Function reply", "函数答复")} data-function-answer>
            <div className="runtime-program-conclusion-summary message-content chat-text"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(answer) }} />
          </section>
        ) : null}
        {conclusion ? (
          <section
            className={`runtime-program-conclusion is-${conclusion.tone}`}
            aria-label={conclusion.label}
          >
            <div className="runtime-program-conclusion-label">
              {conclusion.label}
            </div>
            <p className="runtime-program-conclusion-meta">{conclusion.meta}</p>
            {conclusion.summary ? (
              <div
                className="runtime-program-conclusion-summary message-content"
                dangerouslySetInnerHTML={{
                  __html: renderMarkdown(conclusion.summary),
                }}
              />
            ) : null}
            {conclusion.result ? (
              <div
                className="runtime-program-conclusion-result message-content"
                dangerouslySetInnerHTML={{
                  __html: renderMarkdown(conclusion.result),
                }}
              />
            ) : null}
          </section>
        ) : null}
        {footer}
      </div>
    </div>
  );
}
