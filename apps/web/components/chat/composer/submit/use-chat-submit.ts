"use client";

/**
 * Chat turn submit + stop.
 *
 * `submit` first recognizes a whole-input registered function expression,
 * then covers a mid-run queue/steer decision, a slash command, or a normal
 * turn. A normal turn
 * expands long-paste tokens and `@path` mentions, converts pending docs
 * into path mentions plus `type:"document"` attachments, and hands the
 * payload to `sendChatMessage` — the bridge that fires the optimistic user
 * bubble, welcome-hide and running flip before the WS write.
 *
 * `stop` sends `execution.cancel` for the current execution, patches the
 * live assistant to cancelled, and clears runningTask so the send queue
 * drains at 0ms. Server cancelled still wins on reload.
 */
import type { ExecutionCommand } from "@/lib/execution-debugger";
import { useCallback } from "react";

import { useSessionStore } from "@/lib/session-store";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { parseFunctionInvocation } from "@/lib/function-invocation";
import { showToast } from "@/lib/format-utils/toast";
import { enqueueMessage } from "@/lib/state/send-queue";
import { steerQueuedMessage } from "@/lib/state/steer-message";
import { useFunctions } from "@/lib/state/functions-store";
import { buildAttachmentEnvelope } from "@/lib/attachment-marker";
import { expandAtMentions } from "../attach/at-mention";
import { expandPasteTokens, missingPasteIds } from "../paste/paste-store";
import { sendChatMessage } from "./send-chat-message";
import { resolveFnFormSessionId } from "../modes/fn-form/session-target";
import type { useComposerAttachments } from "../attach/use-composer-attachments";
import type { useSlashMenu } from "../slash/use-slash-menu";
import type { FunctionDispatcher } from "../modes/fn-form/use-function-dispatch";

type Attachments = ReturnType<typeof useComposerAttachments>;

export interface ChatSubmitOptions {
  bound: string | null;
  input: string;
  activeChatKey: string | null;
  currentSessionId: string | null;
  isRunning: boolean;
  noEnabledModels: boolean;
  promptNeedModel(): void;
  send(payload: unknown): boolean;
  setComposerInputFor(ownerKey: string | null, value: string): void;
  setHistoryIndex(index: number): void;
  slash: ReturnType<typeof useSlashMenu>;
  pendingImages: Attachments["pendingImages"];
  pendingDocs: Attachments["pendingDocs"];
  clearAttachmentsAfterSubmit: Attachments["clearAfterSubmit"];
  thinking: string;
  toolsEnabled: boolean;
  toolsProfile: string;
  webSearchEnabled: boolean;
  fastEnabled: boolean;
  fastSupported: boolean;
  runningMessageMode: "queue" | "steer";
  dispatchFunction: FunctionDispatcher;
}

export function useChatSubmit({
  bound,
  input,
  activeChatKey,
  currentSessionId,
  isRunning,
  noEnabledModels,
  promptNeedModel,
  send,
  setComposerInputFor,
  setHistoryIndex,
  slash,
  pendingImages,
  pendingDocs,
  clearAttachmentsAfterSubmit,
  thinking,
  toolsEnabled,
  toolsProfile,
  webSearchEnabled,
  fastEnabled,
  fastSupported,
  runningMessageMode,
  dispatchFunction,
}: ChatSubmitOptions) {
  const submit = useCallback(async () => {
    const submitOwnerKey = activeChatKey ?? currentSessionId;
    const trimmed = input.trim();
    const invocation = parseFunctionInvocation(
      trimmed,
      useFunctions.getState().functions,
    );
    if (invocation.kind === "invalid") {
      useSessionStore.getState().openFnForm(invocation.fn, invocation.prefill);
      showToast(`Invalid function call: ${invocation.error}`, { tone: "error" });
      return;
    }
    if (invocation.kind === "valid") {
      if (pendingImages.length > 0 || pendingDocs.length > 0) {
        showToast(
          "Attachments cannot be added to a direct function call. Remove them or send a normal message.",
          { tone: "error" },
        );
        return;
      }
      const accepted = dispatchFunction(invocation.fn, invocation.kwargs);
      if (!accepted) return;
      setComposerInputFor(submitOwnerKey, "");
      setHistoryIndex(-1);
      slash.close();
      return;
    }
    // During a run every plain-text send first gets one retained queue row.
    // Queue mode leaves it there; steer mode marks that same row injecting
    // until steer_ack either accepts it or releases it back to normal drain.
    // (Plain text only — attachments / slash go through the normal path,
    // which is disabled while running.)
    if (isRunning) {
      if (!trimmed || !submitOwnerKey) return;
      const queuedId = enqueueMessage(submitOwnerKey, {
        text: trimmed,
        thinking,
        toolsEnabled,
        toolsProfile,
        webSearchEnabled,
        serviceTier: fastEnabled && fastSupported ? "priority" : undefined,
        background: bound !== null,
        injecting: false,
      }, pendingImages.length + pendingDocs.length);
      if (!queuedId) {
        // 队列只收纯文本；带附件的草稿保持原样并提示，而不是无声 no-op
        // 让用户以为发送坏了。
        const { showToast } = await import("@/lib/format-utils/toast");
        showToast("Attachments can't be queued — stop the current turn or wait for it to finish.");
        return;
      }
      setComposerInputFor(submitOwnerKey, "");
      setHistoryIndex(-1);
      if (runningMessageMode === "steer") {
        void steerQueuedMessage(submitOwnerKey, queuedId);
      }
      return;
    }
    // Allow image-only submits — the LLM can answer "describe this
    // screenshot" without text. Otherwise require at least one of
    // text or attached image.
    if (!trimmed && pendingImages.length === 0 && pendingDocs.length === 0) {
      return;
    }
    // No enabled model → don't send. Routing a turn with nothing
    // enabled would silently run on a pinned default (the user disabled
    // everything on purpose). Point them at the top-bar picker instead.
    if (noEnabledModels) {
      promptNeedModel();
      return;
    }
    // Block submit while any attachment is still being decoded — the
    // placeholder chips have empty ``attachment.data`` / null
    // ``content``, which would deliver broken payloads. The user
    // sees the chips in a loading shimmer; they just need to wait.
    if (pendingImages.some((p) => p.loading)
        || pendingDocs.some((d) => d.loading)) {
      return;
    }
    // Slash dispatch is decided by the REGISTRY (runCommand returns false
    // when the first word names nothing), not by whether the menu happens
    // to be open: the menu closes on the space that begins the arguments,
    // so gating on it sent every `/cmd <args>` to the model as prose. A
    // message that merely starts with `/` still goes out as a normal turn.
    if (slash.runCommand(trimmed)) {
      setComposerInputFor(submitOwnerKey, "");
      slash.close();
      return;
    }
    // Block submit if any paste token in the draft has lost its
    // backing content. The chip row renders these in red and the
    // ``sendDisabled`` guard below also disables the send button, but
    // re-check here so an Enter-key submit can't slip through if the
    // chip refresh hadn't fired yet.
    if (missingPasteIds(trimmed).size > 0) return;
    // Expand long-paste tokens (``[Pasted #N +M lines]``) back into
    // the outgoing text so the LLM receives the real content. The
    // entries stay in the store — they're now GC'd by the
    // composerDrafts effect once no draft references them anymore.
    let expanded = expandPasteTokens(trimmed);
    // Then expand any ``@path`` mentions by reading the files via the
    // worker's HTTP API. Mentions that fail to read stay as the
    // original ``@path`` token (no silent data loss).
    try {
      const mentionResult = await expandAtMentions(expanded, null);
      expanded = mentionResult.text;
    } catch {
      /* network blip — fall through with raw text */
    }
    // Attached docs are referenced by PATH, never inlined. Electron files
    // use the original native path captured at drop/pick time. Plain-browser
    // files have no source path, so their bytes ride as a document attachment
    // and the backend appends the saved session-workdir path. Images retain
    // their multimodal payload and get an original-path marker when available.
    const { mentions, imagesPayload, docsPayload } = buildAttachmentEnvelope(
      pendingImages,
      pendingDocs,
    );
    if (mentions.length > 0) {
      expanded = `${mentions.join("\n")}\n\n${expanded}`;
    }
    const attachmentsPayload = [...imagesPayload, ...docsPayload];
    // Delegate to legacy `sendMessage` (chat.js) so the user bubble +
    // welcome-hide + assistant placeholder + isRunning flip all fire
    // before the WS payload goes out. Composer is just the trigger.
    const handled = sendChatMessage({
      text: expanded,
      sessionId: submitOwnerKey,
      // A bound (split-pane) composer writes the same payload but must not
      // flip the focused shell's welcome/run singletons.
      background: bound !== null,
      attachments: attachmentsPayload.length > 0 ? attachmentsPayload : undefined,
      thinking,
      toolsEnabled,
      toolsProfile,
      webSearchEnabled,
      serviceTier: fastEnabled && fastSupported ? "priority" : undefined,
    });
    // The bridge already writes this exact socket. A false result means the
    // write did not complete; keep the captured draft + attachments intact so
    // the user can retry after reconnect instead of writing the same socket
    // again through the raw helper.
    if (!handled) return;
    setComposerInputFor(submitOwnerKey, "");
    setHistoryIndex(-1);
    // Revoke + clear pending images / docs now that the WS payload
    // is out the door. Hook handles URL.revokeObjectURL for each
    // image's preview blob.
    clearAttachmentsAfterSubmit(submitOwnerKey);
    slash.close();
  }, [
    clearAttachmentsAfterSubmit,
    activeChatKey,
    currentSessionId,
    input,
    isRunning,
    noEnabledModels,
    pendingDocs,
    pendingImages,
    promptNeedModel,
    send,
    setComposerInputFor,
    slash,
    thinking,
    toolsEnabled,
    toolsProfile,
    webSearchEnabled,
    fastEnabled,
    fastSupported,
    runningMessageMode,
    dispatchFunction,
    // ponytail: `bound` and `setHistoryIndex` are stable for a composer
    // instance, so the pre-split dep list omitted them; kept identical.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ]);

  function stop() {
    const targetSessionId = resolveFnFormSessionId(
      currentSessionId,
      activeChatKey,
    );
    if (!targetSessionId) return;
    stopSession(targetSessionId, send);
  }

  return { submit, stop };
}

/**
 * Cancel the run on `sessionId`. Shared by the composer button and the
 * queued-row "cancel current and send now" action. Occupancy is released
 * on cancel intent (Claude Code): the assistant is patched to cancelled
 * and runningTask is cleared at 0ms so a queued message can go out now.
 * A server cancelled record still wins on reload.
 */
export function stopSession(
  targetSessionId: string,
  send: (payload: unknown) => boolean,
): void {
  const store = useSessionStore.getState();
  const task = store.runningTasks[targetSessionId];
  const executionId = task?.execution_id || "";
  const expectedVersion = task?.status_version;
  const commandId = crypto.randomUUID();
  const direct = executionId ? store.messagesById[executionId] : undefined;
  let optimisticMessage = direct;
  if (!optimisticMessage) {
    const ids = store.messageOrder[targetSessionId] || [];
    for (let i = ids.length - 1; i >= 0; i--) {
      const m = store.messagesById[ids[i]];
      if (!m || m.role !== "assistant") continue;
      if (
        m.status === "done" || m.status === "completed"
        || m.status === "cancelled" || m.status === "error"
        || m.status === "cancelling"
      ) break;
      optimisticMessage = m;
      break;
    }
  }
  // 1. Tell the server first so the model HTTP stream can abort.
  if (executionId && typeof expectedVersion === "number") {
    runtimeState._optimisticCancels[commandId] = {
      sessionId: targetSessionId,
      task: { ...task },
      messageId: optimisticMessage?.id,
      previousMessageStatus: optimisticMessage?.status,
    };
    const sent = send({
      type: "execution.command",
      action: "execution.cancel",
      command_id: commandId,
      execution_id: executionId,
      expected_version: expectedVersion,
      payload: {},
    } satisfies ExecutionCommand);
    if (!sent) delete runtimeState._optimisticCancels[commandId];
  } else if (task) {
    // The Stop button can win the ACK/activation race while the task still
    // has only its local placeholder. Keep a session-level pending-stop
    // record so chat_ack
    // cannot revive that turn without first issuing an exact cancel.
    runtimeState._optimisticStops[targetSessionId] = {
      messageId: optimisticMessage?.id,
      previousMessageStatus: optimisticMessage?.status,
    };
  }
  // 2. Patch the live assistant to cancelled. Keep streamed text.
  //    Only the server-issued execution identity can identify the exact
  //    assistant message. A message id is not an execution owner.
  if (direct) {
    const s = direct.status;
    if (s !== "done" && s !== "completed" && s !== "cancelled" && s !== "error") {
      store.updateMessage(targetSessionId, executionId, { status: "cancelled" });
    }
    store.setRunningTaskFor(targetSessionId, null, "always");
    return;
  }
  if (optimisticMessage) {
    store.updateMessage(targetSessionId, optimisticMessage.id, {
      status: "cancelled",
    });
  }
  // 3. Drop the running task so the send queue drains immediately.
  //    Leaving a cancelling flag on the task was the composer lock.
  store.setRunningTaskFor(targetSessionId, null, "always");
}
