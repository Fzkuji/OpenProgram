"use client";

/**
 * Function-form dispatch — the POST /api/function/{name} path.
 *
 * Track A removed the `/run name k=v …` text-command route entirely, so a
 * fn-form submit builds TYPED kwargs and talks to the dispatcher's forced
 * tool-call entry directly. Everything the chat path gets for free (welcome
 * hide, running flag, an optimistic transcript card, channel + project
 * binding on the freshly created session) has to be reproduced here, and
 * rolled back if the POST never lands.
 */
import { useCallback } from "react";

import { useSessionStore } from "@/lib/session-store";
import { sessionAckIsActive, useCenterTabs } from "@/lib/state/center-tabs-store";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { setRunning } from "@/lib/runtime-bridge/ui";
import { setWelcomeVisible } from "@/lib/runtime-bridge/helpers";
import {
  draftChannelChoiceFor,
  draftChannelChoiceHost,
  dropDraftChannelChoice,
} from "@/lib/runtime-bridge/draft-channel-choice";
import { showToast } from "@/lib/format-utils/toast";
import { pushPath } from "@/lib/shallow-nav";
import { useTranslation } from "@/lib/i18n";
import { visibleParams } from "./fn-form";
import { resolveFnFormSessionId, shouldClearLegacyRunning } from "./session-target";
import type { useFnFormState } from "./use-fn-form-state";

type FnFormFunction = NonNullable<
  ReturnType<typeof useSessionStore.getState>["fnFormFunction"]
>;

export interface FnFormSubmitOptions {
  fnFormFunction: FnFormFunction | null;
  fnForm: ReturnType<typeof useFnFormState>;
  currentSessionId: string | null;
  activeChatKey: string | null;
  isRunning: boolean;
  noEnabledModels: boolean;
  promptNeedModel(): void;
  send(payload: unknown): boolean;
  setCurrentConv(sid: string): void;
  /** Start the close animation — mirrors the open, and runs after the POST
   *  has been fired so the form is still mounted while it builds kwargs. */
  handleFnFormClose(): void;
}

export function useFnFormSubmit({
  fnFormFunction,
  fnForm,
  currentSessionId,
  activeChatKey,
  isRunning,
  noEnabledModels,
  promptNeedModel,
  send,
  setCurrentConv,
  handleFnFormClose,
}: FnFormSubmitOptions) {
  const { text } = useTranslation();

  return useCallback(() => {
    if (!fnFormFunction || isRunning) return;
    // Same gate as chat: a function run needs a model to dispatch
    // against. With nothing enabled, prompt for one instead of letting
    // the agent run on a pinned default.
    if (noEnabledModels) {
      promptNeedModel();
      return;
    }
    const fn = fnFormFunction;
    // Build typed kwargs for the new POST /api/function/{name} endpoint.
    // Track A removed the /run text-command path entirely — fn-form
    // submits now talk to the dispatcher's forced tool-call entry instead
    // of round-tripping through the chat WS as `run name k=v ...` text.
    const kwargs: Record<string, unknown> = {};
    for (const p of visibleParams(fn)) {
      const typeParts = String(p.type || "")
        .split("|")
        .map((part) => part.trim());
      const isBool = typeParts.includes("bool") || typeParts.includes("boolean");
      const isInt = typeParts.includes("int");
      const isFloat = typeParts.includes("float") || typeParts.includes("number");
      let v = String(fnForm.values[p.name] ?? "").trim();
      if (!v && isBool) v = "False";
      if (!v && !p.required) continue;
      if (!v && p.required) {
        fnForm.setError(p.name);
        return;
      }
      if (isBool) {
        kwargs[p.name] = v === "True" || v === "true" || v === "1";
      } else if (isInt) {
        const n = parseInt(v, 10);
        kwargs[p.name] = Number.isFinite(n) ? n : v;
      } else if (isFloat) {
        const n = parseFloat(v);
        kwargs[p.name] = Number.isFinite(n) ? n : v;
      } else {
        kwargs[p.name] = v;
      }
    }

    const dispatchSessionId = resolveFnFormSessionId(currentSessionId, activeChatKey);
    const dispatchStore = useSessionStore.getState();
    const pendingProjectKey = dispatchSessionId ?? activeChatKey;
    const pendingProjectId = pendingProjectKey
      ? dispatchStore.pendingProjectsByChat[pendingProjectKey]
      : undefined;
    const body: Record<string, unknown> = { kwargs };
    if (pendingProjectId) body.project_id = pendingProjectId;
    if (dispatchSessionId) body.session_id = dispatchSessionId;
    // "修改后重新运行"：以原调用为锚点 fork 兄弟分支（旧运行保留在
    // ◀ N/M ▶ 切换里），不是在对话尾部追加一次新调用。
    const forkOf = useSessionStore.getState().fnFormForkOf;
    if (forkOf) body.fork_of_node = forkOf;

    // Hide welcome panel right away (matches old sendChatMessage UX).
    const dispatchChannelChoice = draftChannelChoiceFor(draftChannelChoiceHost, dispatchSessionId);
    setWelcomeVisible(false);
    setRunning(true);

    // 0ms feedback (interaction-feedback policy): drop a client-side
    // pending runtime card into the transcript right now so the user sees
    // the function start instead of a blank gap until the ~0.13s hydrate.
    // The dispatcher pre-creates the run's node and a load_session
    // hydrate (chat_ack {function_run:true}) replaces the whole transcript
    // — that setMessages wipes this placeholder's id, so the real card
    // takes its place with no flicker. Only when we already have a session
    // to key it under; a brand-new session's card lands via the post-POST
    // navigate + hydrate.
    let placeholderId: string | null = null;
    if (dispatchSessionId) {
      const store = useSessionStore.getState();
      const startedAt = Date.now();
      placeholderId = `__optimistic_fn__:${fn.name}:${startedAt}`;
      store.appendMessage(dispatchSessionId, {
        id: placeholderId,
        role: "assistant",
        content: "",
        display: "runtime",
        function: fn.name,
        status: "running",
        timestamp: startedAt,
      });
      store.setRunningTaskFor(dispatchSessionId, {
        session_id: dispatchSessionId,
        msg_id: placeholderId,
        func_name: fn.name,
        started_at: startedAt / 1000,
      });
    }

    void fetch(`/api/function/${encodeURIComponent(fn.name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(async (r) => {
        const j = await r.json().catch(() => null);
        if (!r.ok) {
          const msg =
            j && typeof j.error === "string"
              ? j.error
              : `HTTP ${r.status}`;
          throw new Error(msg);
        }
        // POST returns {session_id, msg_id}. If we weren't already
        // bound to a session, navigate to /s/<sid> + flip the store's
        // currentSessionId — without this the runtime placeholder
        // stream-resumes into a session the chat area can't see, and
        // the page stays blank while gui_agent runs in the background.
        const sid = j && j.session_id;
        if (typeof sid === "string" && sid) {
          if (
            dispatchSessionId
            && dispatchChannelChoice?.channel
            && send({
              action: "set_conversation_channel",
              session_id: sid,
              channel: dispatchChannelChoice.channel,
              account_id: dispatchChannelChoice.account_id || "",
            })
          ) {
            dropDraftChannelChoice(draftChannelChoiceHost, dispatchSessionId);
          }
          // Direct fn-form dispatch has no chat_ack event. Bind a Project
          // selected on the provisional tab here, after the endpoint has
          // created/confirmed the session, and consume only that tab's entry.
          const store = useSessionStore.getState();
          const confirmedProjectKey =
            pendingProjectKey && store.pendingProjectsByChat[pendingProjectKey]
              ? pendingProjectKey
              : sid;
          const confirmedProjectId =
            store.pendingProjectsByChat[confirmedProjectKey];
          if (
            confirmedProjectId
            && send({
              action: "set_session_project",
              session_id: sid,
              project_id: confirmedProjectId,
            })
          ) {
            store.takePendingProject(confirmedProjectKey);
            window.dispatchEvent(new Event("project-changed"));
          }
          const shouldActivate = sessionAckIsActive(sid);
          useCenterTabs.getState().markSessionReady(sid);
          if (shouldActivate) {
            runtimeState.currentSessionId = sid;
            if (sid !== currentSessionId) {
              setCurrentConv(sid);
              pushPath(`/s/${encodeURIComponent(sid)}`);
            }
          }
        }
      })
      .catch((err) => {
        console.error("function call failed:", err);
        const store = useSessionStore.getState();
        // Roll back the optimistic pending card + running task — the
        // dispatch never landed, so leaving them would show a card
        // spinning forever with no backing run.
        if (placeholderId && dispatchSessionId) {
          store.truncateFrom(dispatchSessionId, placeholderId);
          store.setRunningTaskFor(dispatchSessionId, null);
        }
        if (shouldClearLegacyRunning(
          dispatchSessionId,
          store.activeChatKey,
          store.currentSessionId,
        )) {
          setRunning(false);
        }
        // Surface the reason to the user. The backend now returns a
        // structured 400 when a non-agentic tool is invoked via
        // fn-form, so without this the only feedback was a silent
        // console line — the chat panel showed nothing.
        const msg = err instanceof Error ? err.message : String(err);
        showToast(
          text(`Function call failed: ${msg}`, `函数调用失败：${msg}`),
          { tone: "error" },
        );
      });
    handleFnFormClose();
  }, [
    currentSessionId,
    activeChatKey,
    fnFormFunction,
    fnForm,
    handleFnFormClose,
    isRunning,
    noEnabledModels,
    promptNeedModel,
    send,
    setCurrentConv,
    // ponytail: `text` is deliberately NOT a dependency — matching the
    // pre-split callback exactly. It only feeds the failure toast, so a
    // language switch mid-session can show the previous locale there
    // until another dep changes. Flagged, not fixed: adding it changes
    // the callback's identity and this refactor is behaviour-preserving.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ]);
}
