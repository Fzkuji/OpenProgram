"use client";

import { useCallback } from "react";

import { showToast } from "@/lib/format-utils/toast";
import { useTranslation } from "@/lib/i18n";
import { runtimeState } from "@/lib/runtime-bridge/state";
import {
  draftChannelChoiceFor,
  draftChannelChoiceHost,
  dropDraftChannelChoice,
} from "@/lib/runtime-bridge/draft-channel-choice";
import { setWelcomeVisible } from "@/lib/runtime-bridge/helpers";
import { setRunning } from "@/lib/runtime-bridge/ui";
import type { AgenticFunction } from "@/lib/session-store";
import { useSessionStore } from "@/lib/session-store";
import { sessionAckIsActive, useCenterTabs } from "@/lib/state/center-tabs-store";
import { pushPath } from "@/lib/shallow-nav";

import { resolveFnFormSessionId, shouldClearLegacyRunning } from "./session-target";

export interface FunctionDispatchOptions {
  forkOf?: string | null;
}

export type FunctionDispatcher = (
  fn: AgenticFunction,
  kwargs: Record<string, unknown>,
  options?: FunctionDispatchOptions,
) => boolean;

interface UseFunctionDispatchOptions {
  currentSessionId: string | null;
  activeChatKey: string | null;
  isRunning: boolean;
  noEnabledModels: boolean;
  promptNeedModel(): void;
  send(payload: unknown): boolean;
  setCurrentConv(sid: string): void;
}

/** The single client entry for a user-started @agentic_function run. */
export function useFunctionDispatch({
  currentSessionId,
  activeChatKey,
  isRunning,
  noEnabledModels,
  promptNeedModel,
  send,
  setCurrentConv,
}: UseFunctionDispatchOptions): FunctionDispatcher {
  const { text } = useTranslation();

  return useCallback((fn, kwargs, options = {}) => {
    if (isRunning) {
      showToast(text(
        "A run is already active. Wait for it to finish or stop it first.",
        "当前已有任务在运行，请等待完成或先停止。",
      ), { tone: "error" });
      return false;
    }
    if (noEnabledModels) {
      promptNeedModel();
      return false;
    }

    const dispatchSessionId = resolveFnFormSessionId(
      currentSessionId,
      activeChatKey,
    );
    const dispatchStore = useSessionStore.getState();
    const pendingProjectKey = dispatchSessionId ?? activeChatKey;
    const pendingProjectId = pendingProjectKey
      ? dispatchStore.pendingProjectsByChat[pendingProjectKey]
      : undefined;
    const body: Record<string, unknown> = { kwargs };
    if (pendingProjectId) body.project_id = pendingProjectId;
    if (dispatchSessionId) body.session_id = dispatchSessionId;
    if (options.forkOf) body.fork_of_node = options.forkOf;

    const dispatchChannelChoice = draftChannelChoiceFor(
      draftChannelChoiceHost,
      dispatchSessionId,
    );
    setWelcomeVisible(false);
    setRunning(true);

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
      .then(async (response) => {
        const payload = await response.json().catch(() => null);
        if (!response.ok) {
          const message = payload && typeof payload.error === "string"
            ? payload.error
            : `HTTP ${response.status}`;
          throw new Error(message);
        }
        const sid = payload && payload.session_id;
        if (typeof sid !== "string" || !sid) return;

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
          dropDraftChannelChoice(
            draftChannelChoiceHost,
            dispatchSessionId,
          );
        }

        const store = useSessionStore.getState();
        const confirmedProjectKey = pendingProjectKey
          && store.pendingProjectsByChat[pendingProjectKey]
          ? pendingProjectKey
          : sid;
        const confirmedProjectId = store.pendingProjectsByChat[
          confirmedProjectKey
        ];
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
      })
      .catch((error) => {
        console.error("function call failed:", error);
        const store = useSessionStore.getState();
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
        const message = error instanceof Error ? error.message : String(error);
        showToast(text(
          `Function call failed: ${message}`,
          `函数调用失败：${message}`,
        ), { tone: "error" });
      });
    return true;
  }, [
    activeChatKey,
    currentSessionId,
    isRunning,
    noEnabledModels,
    promptNeedModel,
    send,
    setCurrentConv,
    text,
  ]);
}
