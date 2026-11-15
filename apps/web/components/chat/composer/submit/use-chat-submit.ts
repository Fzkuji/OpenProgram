"use client";

/**
 * Chat turn submit + stop.
 *
 * `submit` first recognizes a whole-input registered function expression,
 * then a slash command (during a run and while a request waits too), then
 * a reply to a waiting request, a mid-run queue/steer decision, or a normal
 * turn. A normal turn
 * expands long-paste tokens and `@path` mentions, converts pending docs
 * into path mentions plus `type:"document"` attachments, and hands the
 * payload to `sendChatMessage` — the bridge that fires the optimistic user
 * bubble, welcome-hide and running flip before the WS write. With the
 * socket closed the message waits in the send queue instead and goes out
 * after the reconnect.
 *
 * `stop` sends `execution.cancel` for the current execution, patches the
 * live assistant to cancelled, and clears runningTask so the composer is
 * free at once. The send queue waits until the server releases the turn.
 * Server cancelled still wins on reload.
 */
import { sendExecutionCancel, sendRequestCancel } from "@/lib/execution/cancel-execution";
import { getPendingUserRequestId } from "@/lib/chat/send/pending-user-text";
import { useCallback } from "react";

import { selectViewRows, useSessionStore } from "@/lib/session-store";
import { stopOverlay } from "@/lib/chat/transcript/apply-transcript-update";
import { getSocket, runtimeState } from "@/lib/runtime-bridge/state";
import { parseFunctionInvocation } from "@/lib/execution/function-invocation";
import { showToast } from "@/lib/format-utils/toast";
import { enqueueMessage, holdDrainAfterStop, holdSteeringForStop, useSendQueue } from "@/lib/chat/send/send-queue";
import { steerQueuedMessage } from "@/lib/chat/send/steer-message";
import { openDecisionFor, sendWaitAnswer } from "@/lib/chat/decisions/answer-wait";
import { translateText } from "@/lib/i18n";
import { useFunctions } from "@/lib/abilities/functions-store";
import { buildAttachmentEnvelope } from "@/lib/chat/send/attachment-marker";
import { attachmentsBlockSend } from "../attach/attachment-session-cache";
import { expandAtMentions, type MentionScope } from "../attach/mention/at-mention";
import { expandPasteTokens, expandPasteTokensDelimited, missingPasteIds, takePastedSpans } from "../paste/paste-store";
import { elementsForBody } from "@/lib/chat/send/paste-elements";
import { sendChatMessage } from "./send-chat-message";
import { composerKey, recordingPlaceholder, useRecordingStore } from "@/lib/recording/recording-store";
import { defaultScrollerKey, noteTakeLatest } from "@/lib/chat/history/chat-scroll";
import { resolveFnFormSessionId } from "../modes/fn-form/session-target";
import type { SubmittedAttachments, useComposerAttachments } from "../attach/use-composer-attachments";
import type { useSlashMenu } from "../slash/use-slash-menu";
import type { FunctionDispatcher } from "../modes/fn-form/use-function-dispatch";
import { isSettled } from "@/lib/chat/transcript/row-status";
import { operationCancelCommand } from "@/lib/chat/run/run-operation";

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
  detachAttachmentsForSubmit: Attachments["detachForSubmit"];
  thinking: string;
  webSearchEnabled: boolean;
  fastEnabled: boolean;
  fastSupported: boolean;
  dispatchFunction: FunctionDispatcher;
  /** Where `@path` mentions resolve: this chat's working directory. */
  mentionScope?: MentionScope | null;
}

/** Where a message typed during a run goes: `steer` adds it to the current
 *  turn at its next safe point (Enter, the send button); `queue` holds it
 *  for the next turn (⌘/Ctrl+Enter). */
export type RunningSendMode = "queue" | "steer";

// Preparation belongs to the draft owner across focused and bound composers.
const preparingSends = new Map<string | null, { token: symbol; intent: string; preserveDraft: boolean }>();

function socketOpen(): boolean {
  return getSocket()?.readyState === WebSocket.OPEN;
}

function draftOf(owner: string | null): string {
  const drafts = useSessionStore.getState().composerDrafts;
  return (owner ? drafts[owner] : drafts.__new__) ?? "";
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
  detachAttachmentsForSubmit,
  thinking,
  webSearchEnabled,
  fastEnabled,
  fastSupported,
  dispatchFunction,
  mentionScope = null,
}: ChatSubmitOptions) {
  // A chat-type slash command (/skill, /task, /merge) is an ordinary turn:
  // it takes the same path as a typed message, and during a run, while a
  // request waits or while disconnected it waits in the send queue.
  const sendSlashChat = useCallback((text: string): boolean => {
    const owner = activeChatKey ?? currentSessionId;
    if (!owner) return false;
    const settings = {
      thinking,
      webSearchEnabled,
      serviceTier: fastEnabled && fastSupported ? "priority" : "default",
      background: bound !== null,
    };
    if (isRunning || openDecisionFor(owner) || !socketOpen()) {
      return Boolean(enqueueMessage(owner, { text, ...settings, injecting: false }));
    }
    return sendChatMessage({ text, sessionId: owner, ...settings });
  }, [activeChatKey, currentSessionId, thinking, webSearchEnabled, fastEnabled, fastSupported, bound, isRunning]);

  // A backend-run command (/goal, /rename …) belongs to this composer's
  // conversation like a typed message does. When idle it takes the same
  // send: a draft's local_* id and its permission mode, Agent, project and
  // folders ride along, so the server runs it in the draft (creating the
  // conversation under that id when it starts a turn) and the bubble shows
  // as soon as the server accepts it as a message (chat_ack): a command
  // that only answers (/goal status) leaves its reply line and no bubble.
  // During a run or a waiting request it goes now, beside the turn: the
  // server runs it before it reserves one.
  const sendSlashCommand = useCallback((text: string): boolean => {
    const owner = activeChatKey ?? currentSessionId;
    if (isRunning || openDecisionFor(owner)) {
      return send({ action: "chat", ...(owner ? { session_id: owner } : {}), text });
    }
    return sendChatMessage({
      text,
      sessionId: owner,
      thinking,
      webSearchEnabled,
      serviceTier: fastEnabled && fastSupported ? "priority" : "default",
      background: bound !== null,
      bubble: false,
    });
  }, [activeChatKey, currentSessionId, thinking, webSearchEnabled, fastEnabled, fastSupported, bound, isRunning, send]);

  /** Run `text` as a slash command. False when it names no command; true
   *  when it was one (the draft is cleared only when it ran). */
  const runSlash = useCallback((text: string): boolean => {
    const owner = activeChatKey ?? currentSessionId;
    const outcome = slash.runCommand(text, {
      busy: isRunning || Boolean(openDecisionFor(owner)),
      sendChat: sendSlashChat,
      sendCommand: sendSlashCommand,
    });
    if (!outcome) return false;
    if (outcome === "ran") {
      setComposerInputFor(owner, "");
      setHistoryIndex(-1);
    }
    slash.close();
    return true;
  }, [activeChatKey, currentSessionId, slash, isRunning, sendSlashChat, sendSlashCommand, setComposerInputFor, setHistoryIndex]);

  const submit = useCallback(async (runningMode: RunningSendMode = "steer") => {
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
      if (submitOwnerKey) {
        noteTakeLatest({
          sessionId: submitOwnerKey,
          scrollerKey: defaultScrollerKey(submitOwnerKey, bound !== null),
          turnSeed: `slash:${Date.now()}`,
        });
      }
      setComposerInputFor(submitOwnerKey, "");
      setHistoryIndex(-1);
      slash.close();
      return;
    }
    // Slash dispatch is decided by the REGISTRY (runCommand returns false
    // when the first word names nothing), not by whether the menu happens
    // to be open: the menu closes on the space that begins the arguments,
    // so gating on it sent every `/cmd <args>` to the model as prose. A
    // message that merely starts with `/` still goes out as a normal turn.
    // It comes before the run and request checks below: `/compact 5000`
    // typed during a run is still a command, not text for the model.
    if (trimmed.startsWith("/") && runSlash(trimmed)) return;
    // A run that waits on a request in this conversation takes what is typed
    // here as the reply to it (the card's "tell the agent instead"): a new
    // turn would leave that run waiting behind it.
    const waiting = !isRunning ? openDecisionFor(submitOwnerKey) : null;
    if (waiting && submitOwnerKey) {
      if (!trimmed) return;
      if (pendingImages.length > 0 || pendingDocs.length > 0) {
        showToast(translateText(
          "The agent is waiting on the request above. Answer it, or send text without attachments to reply.",
          "agent 正在等上面的请求。先答复它，或者只发文字作为回复。"), { tone: "error" });
        return;
      }
      if (missingPasteIds(trimmed).size > 0) return;
      const reply = expandPasteTokens(trimmed);
      setComposerInputFor(submitOwnerKey, "");
      setHistoryIndex(-1);
      const sent = await sendWaitAnswer(waiting, "execution.wait.decline", reply, translateText,
        (id) => useSessionStore.getState().dequeueDecision(id));
      // Not delivered: the text goes back into the box, never lost, unless
      // something newer was typed there meanwhile. It goes back as typed,
      // paste chips included, while their content is still kept.
      if (!sent && !draftOf(submitOwnerKey).trim()) {
        setComposerInputFor(submitOwnerKey, missingPasteIds(trimmed).size > 0 ? reply : trimmed);
      }
      return;
    }
    // During a run every plain-text send first gets one retained queue row.
    // A queue send leaves it there; a steer send marks that same row injecting
    // until the durable command receipt accepts it or releases it for normal drain.
    // Attachments travel with either: saved to the session and named by path.
    // With the socket closed a message waits in the same queue, and the
    // reconnect sends it.
    const disconnected = !isRunning && !socketOpen();
    if (isRunning || disconnected) {
      if (useRecordingStore.getState().pending[composerKey(bound)]) {
        showToast(isRunning
          ? "Send the recording after the current turn finishes."
          : "Not connected. Send the recording after the connection is back.", { tone: "error" });
        return;
      }
      if ((!trimmed && pendingImages.length === 0 && pendingDocs.length === 0) || !submitOwnerKey) return;
      if (disconnected && noEnabledModels) {
        promptNeedModel();
        return;
      }
      if (attachmentsBlockSend(pendingImages, pendingDocs)) return;
      // Queue and steer bypass the ordinary send below. Capture the full
      // paste now: clearing the draft may GC its backing entry, and neither
      // queue drain nor steer may send an unresolved placeholder.
      if (missingPasteIds(trimmed).size > 0) return;
      const intent = JSON.stringify({ queue: input,
        images: pendingImages.map(({ id }) => id), docs: pendingDocs.map(({ id }) => id) });
      if (preparingSends.has(submitOwnerKey)) {
        if (preparingSends.get(submitOwnerKey)!.intent !== intent) {
          showToast("An earlier message is still preparing. This draft was not sent; send it again when preparation finishes.", { tone: "error" });
        }
        return;
      }
      const preparationToken = Symbol("queue preparation");
      preparingSends.set(submitOwnerKey, { token: preparationToken, intent, preserveDraft: false });
      try {
        // The queued text is what a sent message would carry: `@path`
        // mentions resolved, pasted spans recorded to show collapsed.
        let body = expandPasteTokensDelimited(trimmed);
        try {
          body = (await expandAtMentions(body, mentionScope)).text;
        } catch {
          /* network blip — queue the raw text */
        }
        const pasted = takePastedSpans(body);
        const textElements = elementsForBody(pasted.text, pasted.spans);
        const queuedId = enqueueMessage(submitOwnerKey, {
          text: pasted.text,
          ...(textElements.length > 0 ? { textElements } : {}),
          images: pendingImages,
          docs: pendingDocs,
          thinking,
          webSearchEnabled,
          serviceTier: fastEnabled && fastSupported ? "priority" : "default",
          background: bound !== null,
          injecting: false,
        }, pendingImages.length + pendingDocs.length);
        if (!queuedId) return;
        clearAttachmentsAfterSubmit(submitOwnerKey, {
          imageIds: pendingImages.map(image => image.id),
          docIds: pendingDocs.map(doc => doc.id),
        });
        // This accepted submit does not reach sendChatMessage until queue drain
        // (and a successful steer may never reach it). Follow the submitting
        // scroller now, even though messageOrder/lastId has not changed yet.
        noteTakeLatest({
          sessionId: submitOwnerKey,
          scrollerKey: defaultScrollerKey(submitOwnerKey, bound !== null),
          turnSeed: `queue:${queuedId}`,
        });
        // Text typed while the mentions resolved stays in the box.
        if (draftOf(submitOwnerKey) === input) setComposerInputFor(submitOwnerKey, "");
        setHistoryIndex(-1);
        if (isRunning && runningMode === "steer") {
          void steerQueuedMessage(submitOwnerKey, queuedId);
        } else if (isRunning) {
          // The run may have ended while the mentions resolved.
          useSendQueue.getState().drain(submitOwnerKey);
        }
      } finally {
        if (preparingSends.get(submitOwnerKey)?.token === preparationToken) {
          preparingSends.delete(submitOwnerKey);
        }
      }
      return;
    }
    // Allow image-only submits — the LLM can answer "describe this
    // screenshot" without text. Otherwise require at least one of
    // text, an attached image or a recording.
    const recordingKey = composerKey(bound);
    const recording = useRecordingStore.getState().pending[recordingKey];
    if (!trimmed && pendingImages.length === 0 && pendingDocs.length === 0 && !recording) {
      return;
    }
    // No enabled model → don't send. Routing a turn with nothing
    // enabled would silently run on a pinned default (the user disabled
    // everything on purpose). Point them at the top-bar picker instead.
    if (noEnabledModels) {
      promptNeedModel();
      return;
    }
    // Block submit while any attachment is still being decoded or a
    // read failed. Sending would drop the item or deliver an empty
    // payload; keep the draft so the user can wait, retry, or remove.
    if (attachmentsBlockSend(pendingImages, pendingDocs)) {
      return;
    }
    // Block submit if any paste token in the draft has lost its
    // backing content. The chip row renders these in red and the
    // ``sendDisabled`` guard below also disables the send button, but
    // re-check here so an Enter-key submit can't slip through if the
    // chip refresh hadn't fired yet.
    if (missingPasteIds(trimmed).size > 0) return;
    const intent = JSON.stringify({ input,
      images: pendingImages.map(({ id, order, sourcePath }) => ({ id, order, sourcePath })),
      docs: pendingDocs.map(({ id, order, sourcePath }) => ({ id, order, sourcePath })), thinking,
      webSearchEnabled, fastEnabled, fastSupported });
    const previous = preparingSends.get(submitOwnerKey);
    if (previous) {
      if (previous.intent !== intent) {
        previous.preserveDraft = true;
        showToast("An earlier message is still preparing. This draft was not sent; send it again when preparation finishes.", { tone: "error" });
      }
      return;
    }
    const preparationToken = Symbol("send preparation");
    const preparation = { token: preparationToken, intent, preserveDraft: false };
    preparingSends.set(submitOwnerKey, preparation);
    try {
      // Expand long-paste tokens (``[Pasted #N +M lines]``) back into
      // the outgoing text so the LLM receives the real content. The
      // entries stay in the store — they're now GC'd by the
      // composerDrafts effect once no draft references them anymore.
      // Pasted spans stay delimited until every rewrite has run, so their
      // final position can be recorded for the collapsed bubble.
      let expanded = expandPasteTokensDelimited(trimmed);
      // Then resolve ``@path`` mentions to references via the worker's
      // HTTP API. Mentions that fail to resolve stay as the
      // original ``@path`` token (no silent data loss).
      try {
        const mentionResult = await expandAtMentions(expanded, mentionScope);
        expanded = mentionResult.text;
      } catch {
        /* network blip — fall through with raw text */
      }
      const pasted = takePastedSpans(expanded);
      expanded = pasted.text;
      // Measured on the body: attachment markers are prepended below, and
      // the records count from the end of the message.
      const textElements = elementsForBody(expanded, pasted.spans);
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
      const capturedAttachmentIds = {
        imageIds: pendingImages.map((image) => image.id),
        docIds: pendingDocs.map((doc) => doc.id),
      };
      const originalDraft = input;
      const hasSubmittedAttachments = pendingImages.length + pendingDocs.length > 0;
      // Set once the socket accepts the frame: the files leave the composer
      // with the text, and only an ACK or a rejection decides their fate.
      let submittedAttachments: SubmittedAttachments | null = null;
      // Delegate to legacy `sendMessage` (chat.js) so the user bubble +
      // welcome-hide + assistant placeholder + isRunning flip all fire
      // before the WS payload goes out. Composer is just the trigger.
      if (recording) {
        // The optimistic bubble shows the recording card until the server's
        // message, which carries the real block, replaces it.
        expanded = expanded ? `${expanded}\n\n${recordingPlaceholder(recording)}` : recordingPlaceholder(recording);
      }
      const handled = sendChatMessage({
        recording: recording?.id,
        text: expanded,
        textElements: textElements.length > 0 ? textElements : undefined,
        sessionId: submitOwnerKey,
        // A bound (split-pane) composer writes the same payload but must not
        // flip the focused shell's welcome/run singletons.
        background: bound !== null,
        attachments: attachmentsPayload.length > 0 ? attachmentsPayload : undefined,
        thinking,
        webSearchEnabled,
        serviceTier: fastEnabled && fastSupported ? "priority" : "default",
        // Native path-only docs have no inline WS attachment payload, but they
        // still belong to this attachment turn for run_active handling.
        hasAttachments: hasSubmittedAttachments,
        onAck: () => {
          if (recording && useRecordingStore.getState().pending[recordingKey]?.id === recording.id) {
            useRecordingStore.getState().setPending(recordingKey, null);
          }
          // Remove only the submitted attachments; keep newer draft edits.
          if (submittedAttachments) submittedAttachments.release();
          else clearAttachmentsAfterSubmit(submitOwnerKey, capturedAttachmentIds);
        },
        onSent: () => {
          if (hasSubmittedAttachments) {
            submittedAttachments = detachAttachmentsForSubmit(submitOwnerKey, capturedAttachmentIds);
          }
          const currentDraft = submitOwnerKey
            ? useSessionStore.getState().composerDrafts[submitOwnerKey] ?? ""
            : useSessionStore.getState().composerDrafts.__new__ ?? "";
          if (!preparation.preserveDraft && currentDraft === originalDraft) {
            setComposerInputFor(submitOwnerKey, "");
          }
        },
        onReject: () => {
          submittedAttachments?.restore();
          const currentDraft = submitOwnerKey
            ? useSessionStore.getState().composerDrafts[submitOwnerKey] ?? ""
            : useSessionStore.getState().composerDrafts.__new__ ?? "";
          if (!currentDraft) setComposerInputFor(submitOwnerKey, originalDraft);
        },
      });
      // The bridge already writes this exact socket. A false result means the
      // write did not complete; keep the captured draft + attachments intact so
      // the user can retry after reconnect instead of writing the same socket
      // again through the raw helper.
      if (!handled) return;
      setHistoryIndex(-1);
      slash.close();
    } finally {
      if (preparingSends.get(submitOwnerKey)?.token === preparationToken) {
        preparingSends.delete(submitOwnerKey);
      }
    }
  }, [
    clearAttachmentsAfterSubmit,
    detachAttachmentsForSubmit,
    activeChatKey,
    currentSessionId,
    input,
    isRunning,
    noEnabledModels,
    pendingDocs,
    pendingImages,
    promptNeedModel,
    setComposerInputFor,
    slash,
    thinking,
    webSearchEnabled,
    fastEnabled,
    fastSupported,
    dispatchFunction,
    bound,
    setHistoryIndex,
    mentionScope,
    runSlash,
  ]);

  function stop() {
    const targetSessionId = resolveFnFormSessionId(
      currentSessionId,
      activeChatKey,
    );
    if (!targetSessionId) return;
    stopSession(targetSessionId, send);
  }

  return { submit, stop, runSlash };
}

/**
 * Cancel the run on `sessionId`. Shared by the composer button, Esc in
 * the composer and the queued-row "cancel current and send now" action.
 * Occupancy is released on cancel intent (Claude Code): the assistant is
 * patched to cancelled and runningTask is cleared at 0ms so the composer
 * is free at once. Queued messages wait until the server has released the
 * turn (holdDrainAfterStop); sent earlier, the server refuses them.
 * A server cancelled record still wins on reload.
 */
export function stopSession(
  targetSessionId: string,
  send: (payload: unknown) => boolean,
): void {
  const store = useSessionStore.getState();
  const task = store.runningTasks[targetSessionId];
  const operationCancel = operationCancelCommand(targetSessionId, task);
  if (operationCancel) {
    // /compact or a rewind has no execution: Stop names the operation.
    if (!send(operationCancel)) {
      showToast("Could not send the stop request. Reconnect and try again.", { tone: "error" });
      return;
    }
    holdDrainAfterStop(targetSessionId);
    showToast("Stop requested. Cancelling the current operation…");
    store.setRunningTaskFor(targetSessionId, null, "never");
    return;
  }
  const waiting = task ? null : openDecisionFor(targetSessionId);
  if (waiting) {
    // The run waits on a request: stopping it cancels that run.
    const sent = sendExecutionCancel(send, waiting.executionId, waiting.expectedVersion);
    if (!sent) {
      showToast("Could not send the stop request. Reconnect and try again.", { tone: "error" });
      return;
    }
    store.dequeueDecision(waiting.id);
    holdDrainAfterStop(targetSessionId, waiting.executionId);
    showToast("Stop requested. Cancelling the current operation…");
    return;
  }
  const executionId = task?.execution_id || "";
  const expectedVersion = task?.status_version;
  const commandId = crypto.randomUUID();
  const rows = selectViewRows(targetSessionId)(store);
  const direct = executionId ? rows[executionId] : undefined;
  let optimisticMessage = direct;
  if (!optimisticMessage) {
    const ids = store.messageOrder[targetSessionId] || [];
    for (let i = ids.length - 1; i >= 0; i--) {
      const m = rows[ids[i]];
      if (!m || m.role !== "assistant") continue;
      if (isSettled(m.status) || m.status === "cancelling") break;
      optimisticMessage = m;
      break;
    }
  }
  // 1. Tell the server first so the model HTTP stream can abort. A stop
  //    names its execution; the version is only what the page last saw
  //    (a reconnect's run state may hand over the ids alone).
  const cancelSent = Boolean(executionId);
  if (executionId) {
    runtimeState._optimisticCancels[commandId] = {
      sessionId: targetSessionId,
      task: { ...task },
      messageId: optimisticMessage?.id,
      previousMessageStatus: optimisticMessage?.status,
    };
    // A transport exception does not establish that the run stopped.
    const sent = sendExecutionCancel(send, executionId, typeof expectedVersion === "number" ? expectedVersion : 0, commandId);
    if (!sent) {
      delete runtimeState._optimisticCancels[commandId];
      showToast("Could not send the stop request. The task may still be running; reconnect and try again.", { tone: "error" });
      // Keep the task, reply and queue intact so Stop remains retryable.
      return;
    }
  } else if (task) {
    // The Stop button can win the ACK/activation race while the task still
    // has only its local placeholder. The stop names the send at once (the
    // Worker keeps it for the send's admission); the session-level record
    // keeps chat_ack from showing that turn as running again.
    // A function run or retry names its own request; a chat send the one
    // waiting for its ACK.
    const requestId = task.client_request_id || getPendingUserRequestId(targetSessionId);
    const byRequest = Boolean(requestId)
      && sendRequestCancel(send, targetSessionId, requestId as string, commandId);
    runtimeState._optimisticStops[targetSessionId] = {
      messageId: optimisticMessage?.id,
      previousMessageStatus: optimisticMessage?.status,
      ...(byRequest ? { commandId } : {}),
    };
  }
  if (task) {
    holdSteeringForStop(targetSessionId);
    holdDrainAfterStop(targetSessionId, executionId);
  }
  if (task) showToast("Stop requested. Cancelling the current operation…");
  // 2. Patch the live assistant to cancelled. Keep streamed text.
  //    Only the server-issued execution identity can identify the exact
  //    assistant message. A message id is not an execution owner.
  const stopClears = cancelSent ? [{ until_command_rejected: commandId }] : [];
  if (direct) {
    const s = direct.status;
    if (s !== "done" && s !== "completed" && s !== "cancelled" && s !== "error") {
      store.applyLocalChange(targetSessionId, stopOverlay(executionId, stopClears));
    }
    store.setRunningTaskFor(targetSessionId, null, "always");
    return;
  }
  if (optimisticMessage) {
    store.applyLocalChange(targetSessionId, stopOverlay(optimisticMessage.id, stopClears));
  }
  // 3. Drop the running task so the send queue drains immediately.
  //    Leaving a cancelling flag on the task was the composer lock.
  store.setRunningTaskFor(targetSessionId, null, "always");
}
