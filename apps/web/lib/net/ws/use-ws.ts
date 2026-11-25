"use client";
import { callMobile, mobileBridge } from "@/lib/mobile/bridge";
import { cachedSessionList } from "@/lib/mobile/session-list-cache";
import { dispatchChanged, dispatchReconnected } from "@/lib/net/ws/changes";
import { sameSettingsBranch } from "@/lib/session-store/branch-settings";
import { requestSessionLoad, acceptSessionLoad, failSessionLoad, disposeSessionLoads } from "@/lib/runtime-bridge/session/session-load";

/**
 * Chat WebSocket lifecycle — React owner.
 *
 * React owns the WebSocket connection. All message types are dispatched
 * here — known types have explicit handlers, unknown types are surfaced
 * as `op:ws-message` window events for component-level listeners. The
 * live socket is published through `runtimeState` so `wsSend` helpers
 * and non-React modules can reach it.
 */
import { useEffect } from "react";
import { interfaceWindowId, receiveInterface } from "@/lib/framework/connection";
import { createExecutionRecovery, shouldReleaseAfterExecution } from "../execution/execution-recovery";
import { createHistoryFragmentDecoder } from "./history-fragments";
import { noteFrontendBuild } from "./frontend-reload";
import { executionMessageIds, pendingExecutionReplayRequests } from "../execution/execution-message-recovery";
import { useFunctions } from "@/lib/abilities/functions-store";

import { permissionSnapshotPatch } from "@/lib/session-store/permission-state";
import { consumeCommandErrorFrame } from "@/lib/net/action-error";
import type {
  PermissionRulesDetail,
  JobStatusDetail,
} from "@/lib/net/ws/ws-events";
import { useSessionStore, type PendingDecision } from "@/lib/session-store";
import {
  loadSessionData,
  onBranchCheckedOut,
  onWorkspaceAlignmentResolved,
  onBranchesListMessage,
  onChannelAccountsMessage,
} from "@/lib/runtime-bridge/session/conversations";
import {
  handleRunningTask,
  handleRunningTaskClear,
  restoreForegroundExecutionTask,
  handleExecutionCommandUpdated,
  handleSessionsList,
  handleSessionUpdated,
  initChatPage,
  wsHandleChatAck,
  wsHandleChatResponse,
  wsHandleStatus,
} from "@/lib/runtime-bridge/session/chat-handlers";
import { mirrorUpsertConv } from "@/lib/runtime-bridge/session/conv-store-mirror";
import { runtimeState, setSocket } from "@/lib/runtime-bridge/state";
import { applyChatWsMessage, clearSessionByMsgId } from "@/lib/net/ws/chat-stream";
import { flushTextFrames } from "@/lib/net/ws/text-frames";
import { noteJobStatus } from "@/lib/chat/transcript/sub-agent-transcripts";
import { HISTORY_PROTOCOL, useSessionHistory } from "@/lib/chat/history/session-history";
import { applyTranscriptUpdate, handleViewClosed, repairSessionViews } from "@/lib/runtime-bridge/session/transcript-updates";
import { acceptRunFrame } from "@/lib/chat/decisions/run-seq";
import { applyRunState, listedRunStates, unlistedRunning, type RunStateData } from "@/lib/chat/run/running-sessions";
import { waitForOwnerAuthBootstrap } from "@/lib/net/auth/owner-auth-bootstrap";
import { recoverOwnerAuth } from "@/lib/net/auth/owner-auth-recovery";
import { showToast } from "@/lib/format-utils/toast";
import { notifyDesktopSessionLoaded } from "@/lib/desktop/self-update-reopen";
import { translateText } from "@/lib/i18n";
import { getQueryClient } from "@/lib/query-client";
import { applyProviderQuotaFrame } from "@/lib/models/provider-state";
import {
  agentSettingsChanged,
  loadAgentSettings,
  updateAgentBadges,
  updateProviderBadge,
} from "@/lib/runtime-bridge/providers";
import { addSystemMessage, formatProviderLabel } from "@/lib/runtime-bridge/helpers";
import {
  loadProgramsMeta,
  renderFunctions,
} from "@/lib/runtime-bridge/actions/functions-panel";
import { refreshStatusSource, setRunning, updateStatus } from "@/lib/runtime-bridge/ui";
import { refreshChannelBadge } from "@/lib/runtime-bridge/session/conversations";
import { onMachinesMessage } from "@/lib/machines/store";
import { loadExecutionCursors, recordExecutionCursor } from "@/lib/net/execution/execution-cursor";
import { pushStatusBadge } from "@/lib/tabs/top-bar-sync";
import {
  forgetSystemAccessWait,
  rememberSystemAccessWait,
} from "@/lib/access/system-access-wait-state";
import {
  clearPendingFirstAck,
  clearPendingUserText,
  getPendingUserMessageId,
  hasPendingFirstAck,
  hasPendingUserText,
} from "@/lib/chat/send/pending-user-text";
import { frameNamesPendingSend, markSendNotSent, settleSendsOnDisconnect } from "@/lib/net/ws/unacked-sends";

/** Release only a chat turn rejected before chat_ack. The composer owns the
 * draft and attachments, so this intentionally never invokes ACK cleanup. */
export function releaseChatOperationError(
  data?: Record<string, unknown>,
): boolean {
  if (data?.action !== "chat") return false;
  const sid = typeof data.session_id === "string" ? data.session_id : "";
  if (!sid) return false;
  if (!hasPendingUserText(sid) && !hasPendingFirstAck(sid)) return false;
  if (!frameNamesPendingSend(sid, data)) return false;
  markSendNotSent(sid, getPendingUserMessageId(sid), typeof data.code === "string" ? data.code : undefined);
  clearPendingUserText(sid);
  clearPendingFirstAck(sid);
  const task = useSessionStore.getState().runningTasks[sid];
  if (task && !task.execution_id && !task.msg_id) {
    useSessionStore.getState().setRunningTaskFor(sid, null, "always");
  }
  return true;
}

export function useWS(): void {
  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;
    let authNoticeShown = false;
    let connectGeneration = 0;
    // The last time the socket heard anything, and whether the next close
    // reconnects at once (a socket found dead on wake) or after the pause.
    let heardAt = 0;
    let reconnectNow = false;
    let wakeCheck: ReturnType<typeof setTimeout> | undefined;
    let historyDeadline: ReturnType<typeof setTimeout> | undefined;
    let recovery: ReturnType<typeof createExecutionRecovery> | undefined;

    /** React-side dispatch for all WS message types. Known types have
     *  explicit handlers; unknown types are surfaced as `op:ws-message`
     *  window events for component-level listeners. */
    function dispatch(msg: {
      type?: string;
      data?: Record<string, unknown>;
    }, recovered = false): boolean {
      if (msg.type !== "execution.replay") {
        const frame = msg as { execution?: { event_cursor?: unknown; status?: unknown; session_id?: unknown }; event_cursor?: unknown; data?: { event_cursor?: unknown; status?: unknown; session_id?: unknown } };
        const observed = recordExecutionCursor(
          frame.event_cursor ?? frame.execution?.event_cursor ?? frame.data?.event_cursor,
          frame.execution ?? (msg.type === "execution.updated" ? frame.data : undefined),
        );
        if (observed.replayAfter !== undefined) {
          if (observed.cursor) recovery?.request(observed.cursor.execution_id, observed.replayAfter);
          return true;
        }
      }
      if (msg.type === "operation_error" || msg.type === "action_error") {
        releaseChatOperationError(msg.data);
        if (msg.data?.action === "load_session") {
          if (socket) failSessionLoad(socket, msg.data);
          clearTimeout(historyDeadline);
          recovery?.start();
          // An id the server does not know (a stale link, a chat deleted
          // elsewhere): no sidebar row, and its tabs move on as on delete.
          const missing = msg.data.session_id;
          if (msg.data.code === "session_not_found" && typeof missing === "string" && missing) {
            void import("@/lib/tabs/navigation/open-session").then(({ forgetMissingSession }) => {
              forgetMissingSession(missing);
            });
          }
        }
      }
      if (consumeCommandErrorFrame(msg, translateText)) return true;
      if (msg.type === "framework.interface" && socket && msg.data) {
        void receiveInterface(socket, msg.data);
        return true;
      }
      const d = msg.data;
      switch (msg.type) {
        case "pong":
          return true;
        case "chat_ack":
          // Mirror into the React message store, then the
          // session/badge bookkeeping.
          try {
            applyChatWsMessage({ type: "chat_ack", data: d });
          } catch (err) {
            console.error("[useWS] reducer error:", err);
          }
          wsHandleChatAck((d ?? {}) as never);
          return true;
        case "chat_response":
          try {
            applyChatWsMessage({ type: "chat_response", data: d });
          } catch (err) {
            console.error("[useWS] reducer error:", err);
          }
          wsHandleChatResponse((d ?? {}) as never);
          return true;
        case "status":
          wsHandleStatus(msg as never);
          return true;
        // `steer_ack` is consumed by the request-correlated listener in
        // steer-message.ts. It needs no global session-store mutation here.
        case "steer_ack":
          return true;
        case "transcript_update":
          if (d) applyTranscriptUpdate(d as never);
          return true;
        case "transcript_gap":
          if (typeof d?.session_id === "string") repairSessionViews(d.session_id);
          return true;
        case "view_closed":
          if (typeof d?.view === "string") handleViewClosed(d.view);
          return true;
        case "branches_changed": {
          const sid = d?.session_id as string | undefined;
          if (sid && useSessionHistory.getState().pages[sid]) {
            socket?.send(JSON.stringify({ action: "list_branches", session_id: sid }));
          }
          return true;
        }
        case "attach_branch_result": {
          // Failure surface for the Branches panel's "Attach to" action.
          // On success (including duplicate re-attach) the attach card
          // reaches open chats as a transcript update and the branch list
          // as `branches_changed` (ws_actions/branch.py::handle_attach_branch),
          // so only the failure branch needs handling here.
          if (d?.ok !== false) return true;
          // A frame owned by another conversation must not toast into this one.
          const sid = d?.session_id as string | undefined;
          if (sid && sid !== runtimeState.currentSessionId) return true;
          const err = (d?.error as string | undefined) || "unknown error";
          console.error("[useWS] attach_branch failed:", d);
          void import("@/lib/format-utils/toast").then(({ showToast }) => {
            showToast(
              translateText(`Branch attach failed: ${err}`, `分支挂接失败：${err}`),
              { tone: "error" },
            );
          });
          return true;
        }
        case "merge_branches_result": {
          // Failure surface for the Branches panel's merge action. A
          // successful merge reaches a chat as `window_stale` (reason "branch"),
          // which re-fetches the conversation
          // (ws_actions/merge.py::handle_merge_branches), so only the
          // failure branch needs handling here.
          if (!d?.failed) return true;
          const sid = d?.session_id as string | undefined;
          if (sid && sid !== runtimeState.currentSessionId) return true;
          const err = (d?.error as string | undefined) || "unknown error";
          console.error("[useWS] merge_branches failed:", d);
          void import("@/lib/format-utils/toast").then(({ showToast }) => {
            showToast(
              translateText(`Branch merge failed: ${err}`, `分支合并失败：${err}`),
              { tone: "error" },
            );
          });
          return true;
        }
        case "nodes_deleted":
        case "nodes_restored":
          // Replies to the DAG canvas's delete / undo
          // (dag/interaction/selection/node-delete.ts owns the undo stack and the
          // action bar). The transcript learns of the change as a
          // transcript update.
          window.dispatchEvent(new CustomEvent("op:dag-nodes", {
            detail: { type: msg.type, data: d || {} },
          }));
          return true;
        case "branch_renamed":
        case "branch_name_deleted":
        case "branch_deleted": {
          const sid = d?.session_id as string | undefined;
          if (sid) {
            socket?.send(
              JSON.stringify({ action: "list_branches", session_id: sid }),
            );
          }
          return true;
        }
        case "permission_rules": {
          // 权限规则面板刷新：把 session 层规则派给 PermissionsSection。
          window.dispatchEvent(
            new CustomEvent("op:permission-rules", {
              detail: (d ?? {}) as PermissionRulesDetail,
            }),
          );
          return true;
        }
        case "changed": {
          const frame = msg as { topic?: unknown; keys?: unknown };
          if (typeof frame.topic !== "string") return true;
          const keys = Array.isArray(frame.keys) ? frame.keys.filter((k): k is string => typeof k === "string") : null;
          dispatchChanged(frame.topic, keys);
          return true;
        }
        case "skills:changed": {
          // File-system watcher fired — refresh the skills list so the
          // /skills page, Discovery counts, and slash menu reflect the
          // change without any user action.
          import("@/lib/abilities/skills-store").then(({ useSkills }) => {
            useSkills.getState().fetchSkills();
          });
          return true;
        }
        case "plugins:changed":
        case "plugins:update_available":
          // Both mean "the plugins list is stale" — update_available is
          // broadcast by the server's update poll (server.py) and rides
          // the same refresh so the upgrade hint can surface.
          import("@/lib/abilities/plugins-store").then(({ usePluginsStore }) => {
            usePluginsStore.getState().refresh();
          });
          return true;
        case "programs:changed":
          // A harness was installed at runtime (cloned into agentics/ or
          // `programs install`) and the backend re-scanned — refresh the
          // function catalogue so its new functions show up live, no
          // reload needed. Same shape as skills/plugins above.
          import("@/lib/abilities/functions-actions").then(({ refreshFunctionsList }) => {
            refreshFunctionsList();
          });
          return true;
        case "execution.updated": {
          const execution = (msg as { execution?: {
            execution_id?: string;
            session_id?: string;
            status?: string;
            status_version?: number;
            reason_code?: string;
            event_sequence?: number;
            foreground_task?: unknown;
            display?: { user_message_id?: unknown; assistant_message_id?: unknown };
          } }).execution || d;
          if (!execution?.execution_id) return true;
          const eventCursor = (msg as { event_cursor?: unknown }).event_cursor
            ?? (d as { event_cursor?: unknown } | undefined)?.event_cursor;
          window.dispatchEvent(new CustomEvent("op:execution-update", {
            detail: { execution, event_cursor: eventCursor },
          }));
          const eid = String(execution.execution_id);
          const eventSequence = (d as { event_sequence?: number } | undefined)?.event_sequence
            ?? execution.event_sequence;
          const input = (d as { input?: {
            user_message_id?: unknown;
            assistant_message_id?: unknown;
          } } | undefined)?.input;
          const messageIds = executionMessageIds(execution, input);
          if (!useSessionStore.getState().acceptExecutionUpdate(
            eid,
            eventSequence,
            execution.status,
            execution.session_id,
            messageIds,
          )) return true;
          const observedRunningExecution = useSessionStore.getState().runningTasks[String(execution.session_id || "")]?.execution_id === eid;
          if (execution.session_id) {
            const attentionSessionId = String(execution.session_id);
            const attentionStatus = execution.status;
            void import("@/lib/desktop/attention").then(({ reportExecutionStatus }) =>
              reportExecutionStatus(eid, attentionSessionId, attentionStatus));
          }
          restoreForegroundExecutionTask(d?.foreground_task ?? execution.foreground_task, execution);
          {
            const store = useSessionStore.getState();
            const sid = String(execution.session_id || "");
            if (["cancelled", "completed", "failed", "interrupted"].includes(String(execution.status))) {
              for (const decision of store.pendingDecisions) {
                if (decision.executionId === eid) store.dequeueDecision(decision.id);
              }
            }
            let task = sid ? store.runningTasks[sid] : undefined;
            // A task whose execution id is still the reply-id stand-in
            // belongs to this execution when the frame names its turn;
            // without the real id Stop cannot cancel and the end of the
            // run never releases the task.
            if (task && task.execution_id !== eid && task.msg_id
              && messageIds.includes(task.msg_id)) {
              task = { ...task, execution_id: eid };
              store.setRunningTaskFor(sid, task, "never");
            }
            if (
              task
              && task.execution_id === eid
              && typeof execution.status_version === "number"
            ) {
              store.setRunningTaskFor(sid, {
                ...task,
                status_version: execution.status_version,
              }, "never");
            }
            const matches = Boolean(
              task && (
                task.execution_id === eid
              ),
            );
            // cancelling 中间态不写回 runningTask（不许留 cancelling:true，
            // 那会把停止/发送一起禁用并卡住队列）；只在终态收尾。
            if (
              matches
              && (execution.status === "cancelled"
                || execution.status === "completed"
                || execution.status === "failed"
                || execution.status === "interrupted")
            ) {
              store.setRunningTaskFor(sid, null, "always");
            }
            const terminal = new Set(
              ["cancelled", "completed", "failed", "interrupted", "error", "done"],
            );
            if (terminal.has(String(execution.status))) {
              // The page's own stop of this turn is answered here, once the
              // execution reports it ended; a command's answer only says the
              // stop is in force.
              let stopped = false;
              for (const [commandId, pendingCancel] of Object.entries(
                runtimeState._optimisticCancels,
              )) {
                if (
                  pendingCancel.sessionId === sid
                  && pendingCancel.task.execution_id === eid
                ) {
                  stopped = true;
                  delete runtimeState._optimisticCancels[commandId];
                }
              }
              if (stopped && execution.status === "cancelled") showToast("Stopped.");
            }
            // The terminal execution frame releases the composer; the reply's
            // own terminal row reaches the chat as a transcript update. An old
            // execution must not clear a newer task occupying this session.
            const fresh = useSessionStore.getState();
            if (
              sid === runtimeState.currentSessionId
              && shouldReleaseAfterExecution(recovered, observedRunningExecution)
              && terminal.has(String(execution.status))
              && (!fresh.runningTasks[sid]
                || fresh.runningTasks[sid]?.execution_id === eid)
            ) {
              setRunning(false);
            }
          }
          return true;
        }
        case "execution.replay": {
          const replay = d as { snapshot?: Record<string, unknown>; event_cursor?: unknown; recovery?: string } | undefined;
          const snapshot = replay?.snapshot;
          const replayId = (msg as { execution_id?: string }).execution_id ?? snapshot?.execution_id;
          if (typeof replayId === "string") {
            recovery?.complete(replayId, ["cancelled", "completed", "failed", "interrupted", "error", "done"].includes(String(snapshot?.status)));
          }
          if (snapshot && typeof snapshot.execution_id === "string") {
            recordExecutionCursor(replay?.event_cursor, snapshot, true);
            return dispatch({ type: "execution.updated", execution: snapshot, data: snapshot, event_cursor: replay?.event_cursor } as never, true);
          }
          return true;
        }
        case "execution.command.updated":
          // Canonical command frames carry `command` at the envelope root.
          // Do not fall back to the removed legacy nested payload.
          handleExecutionCommandUpdated(msg);
          return true;
        case "running_task": {
          const rd = d as { session_id?: string; run_seq?: unknown } | undefined;
          if (acceptRunFrame(rd?.session_id, rd?.run_seq)) handleRunningTask(d);
          return true;
        }
        case "running_task_clear":
          {
            const rd = d as { session_id?: string; run_seq?: unknown } | undefined;
            if (!acceptRunFrame(rd?.session_id, rd?.run_seq)) return true;
          }
          handleRunningTaskClear(
            (d as { session_id?: string } | undefined)?.session_id,
            {
              execution_id: (d as { execution_id?: string } | undefined)?.execution_id,
              msg_id: (d as { msg_id?: string } | undefined)?.msg_id,
            },
          );
          return true;
        case "job_status": {
          noteJobStatus((d ?? {}) as JobStatusDetail);
          // Async job lifecycle broadcast. Dispatch a
          // window event so any panel listening (BranchesPanel,
          // JobsPanel) can update without prop-drilling. Transcript
          // updates carry the attach changes; this event is purely for
          // the in-flight badge.
          try {
            window.dispatchEvent(
              new CustomEvent("op:job-status", {
                detail: (d ?? {}) as JobStatusDetail,
              }),
            );
          } catch {
            /* defensive: dispatchEvent should not throw */
          }
          return true;
        }
        case "spawn_job_result":
        case "jobs_list":
        case "job":
        {
          // Replies to resource actions. We let the
          // requester correlate via the original send/await pattern
          // (no global handler needed). Surface as a window event
          // so a panel that did issue the request can match by
          // job_id if it wants to.
          try {
            window.dispatchEvent(
              new CustomEvent("op:job-message", {
                detail: { type: msg.type, data: d },
              }),
            );
          } catch {
            /* defensive */
          }
          return true;
        }
        case "provider_info":
        case "provider_changed":
          updateProviderBadge(d as never);
          if (msg.type === "provider_changed") {
            addSystemMessage(
              "Switched to " + formatProviderLabel(d as never),
            );
          }
          return true;
        case "agent_settings_changed": {
          // The frame names no conversation: whose model it carries only a
          // read says. Each chip keeps its model until its own read lands.
          void agentSettingsChanged();
          // Enabled-models may have changed with the settings (Settings
          // toggles broadcast this event) — drop the query cache so every
          // tab's model picker refetches, not just the settings tab.
          getQueryClient()?.invalidateQueries({ queryKey: ["models-enabled"] });
          // A harness read since (installed, signed in, its models) reaches its menus.
          getQueryClient()?.invalidateQueries({ queryKey: ["harnesses"] });
          return true;
        }
        case "provider_quota": {
          // One key's new quota and its provider's picker state: the model
          // picker and its trigger follow it without a request. Settings
          // surfaces listen to the same frame through the window event.
          applyProviderQuotaFrame(getQueryClient(), d as never);
          window.dispatchEvent(new CustomEvent("op:provider-quota", { detail: d }));
          return true;
        }
        case "provider.credentials.changed": {
          // A provider's credential list changed (added, renamed, reordered,
          // cooling, refused): its Credentials section, the card counts and
          // the model menu's key row read it again.
          const provider = typeof d?.provider === "string" ? d.provider : "";
          const queryClient = getQueryClient();
          queryClient?.invalidateQueries({ queryKey: ["provider-credentials", provider] });
          window.dispatchEvent(new CustomEvent("op:provider-credentials-changed", { detail: { provider } }));
          return true;
        }
        case "session.credentials.changed": {
          // A conversation was pinned to a credential, or set back to automatic.
          const sid = typeof d?.session_id === "string" ? d.session_id : "";
          if (sid) getQueryClient()?.invalidateQueries({ queryKey: ["session-credentials", sid] });
          return true;
        }
        case "provider_models_changed": {
          const provider = typeof d?.provider === "string" ? d.provider : "";
          const queryClient = getQueryClient();
          queryClient?.invalidateQueries({ queryKey: ["models-enabled"] });
          queryClient?.invalidateQueries({ queryKey: ["providers"] });
          if (provider) {
            queryClient?.invalidateQueries({ queryKey: ["models", provider] });
          }
          // The current settings surface still owns its provider-model rows
          // as local component state.  Give it the same invalidation signal;
          // this can disappear once that surface is migrated to React Query.
          window.dispatchEvent(new CustomEvent("op:provider-models-changed", {
            detail: { provider },
          }));
          return true;
        }
        case "chat_session_update":
          if (d?.session_id && runtimeState._agentSettings.chat) {
            runtimeState._agentSettings.chat.session_id = d.session_id;
            updateAgentBadges();
          }
          return true;
        case "event":
          return true;
        // runtime.ask/confirm/approval —— 系统停下来等用户决定。入 composer
        // 的 pendingDecisions 队列，由输入框 question/approval mode 承接呈现
        // （docs/reference/design/ui/composer/composer-interaction-modes.html）。不再走独立浮窗。
        case "system_access.waiting":
        case "system_access.resolved":
          if (msg.type === "system_access.waiting" && d) {
            // Only the worker's live request may arm native setup. A
            // session_loaded/reconnect replay is a status restoration and
            // must remain passive until the owner explicitly acts.
            rememberSystemAccessWait(d, d.live === true);
            if (d.live === true && typeof d.wait_id === "string" && typeof d.session_id === "string") {
              const waitId = d.wait_id;
              const waitSessionId = d.session_id;
              void import("@/lib/desktop/attention").then(({ reportAttention }) => reportAttention({
                kind: "system_access", sessionId: waitSessionId, key: `system-access:${waitId}`,
              }));
            }
            for (const request of pendingExecutionReplayRequests([d])) {
              recovery?.request(request.execution_id, request.after_sequence);
            }
          } else if (msg.type === "system_access.resolved" && d) {
            forgetSystemAccessWait(d);
          }
          window.dispatchEvent(new CustomEvent("op:system-access", { detail: { type: msg.type, data: d } }));
          return true;
        case "question.asked":
          import("@/lib/session-store").then(({ useSessionStore }) => {
            const dd = (d || {}) as Record<string, unknown>;
            if (!dd.id) return;
            void import("@/lib/desktop/attention").then(({ reportAttention }) => reportAttention({
              kind: "needs_input",
              sessionId: String(dd.session_id || ""),
              key: `question:${String(dd.id)}`,
              detail: String(dd.prompt || ""),
            }));
            for (const request of pendingExecutionReplayRequests([dd])) {
              recovery?.request(request.execution_id, request.after_sequence);
            }
            useSessionStore.getState().enqueueDecision({
              id: String(dd.id),
              sessionId: String(dd.session_id || ""),
              executionId: String(dd.execution_id || ""),
              waitGeneration: Number(dd.wait_generation || 0),
              expectedVersion: Number(dd.expected_version || 0),
              kind: (dd.kind as "ask" | "confirm" | "approval" | "form" | "ask_many") || "ask",
              prompt: String(dd.prompt || ""),
              options: Array.isArray(dd.options) ? (dd.options as string[]) : [],
              multi: Boolean(dd.multi),
              allow_custom: dd.allow_custom !== false,
              detail: dd.detail ? String(dd.detail) : undefined,
              tool: dd.tool ? String(dd.tool) : undefined,
              args: (dd.args as Record<string, unknown>) || undefined,
              allowedScopes: Array.isArray(dd.allowed_scopes) ? dd.allowed_scopes.filter((scope): scope is string => typeof scope === "string") : undefined,
              risk_level: (dd.risk_level as "low" | "medium" | "high") || undefined,
              askedBy: typeof dd.asked_by === "string" && dd.asked_by ? dd.asked_by : undefined,
              reason: typeof dd.reason === "string" && dd.reason ? dd.reason : undefined,
              writeRoot: typeof dd.write_root === "string" && dd.write_root ? dd.write_root : undefined,
              schema:
                dd.schema && typeof dd.schema === "object"
                  ? (dd.schema as PendingDecision["schema"])
                  : undefined,
              questions: Array.isArray(dd.questions)
                ? (dd.questions as PendingDecision["questions"])
                : undefined,
            });
          });
          return true;
        case "question.replied":
        case "question.rejected":
          import("@/lib/session-store").then(({ useSessionStore }) => {
            const id = (d as Record<string, unknown>)?.id;
            if (id) useSessionStore.getState().dequeueDecision(String(id));
          });
          return true;
        case "functions_list":
          if (Array.isArray(d)) useFunctions.getState().setFunctions(d);
          loadProgramsMeta().then(() => renderFunctions());
          return true;
        case "channel_accounts":
          onChannelAccountsMessage(d as never);
          return true;
        case "machines":
          onMachinesMessage(d);
          return true;
        case "branches_list":
          onBranchesListMessage(d as never);
          return true;
        case "branch_checked_out":
          onBranchCheckedOut(d as never);
          return true;
        case "workspace_alignment_resolved":
          onWorkspaceAlignmentResolved(d as never);
          return true;
        // Broadcast after set_working_dirs succeeds — the backend is the
        // source of truth, so it overwrites any optimistic UI update.
        case "working_dirs":
          import("@/lib/session-store").then(({ useSessionStore }) => {
            const dd = (d || {}) as { session_id?: string; dirs?: unknown };
            if (!dd.session_id) return;
            useSessionStore
              .getState()
              .setAdditionalWorkingDirs(
                dd.session_id,
                Array.isArray(dd.dirs) ? (dd.dirs as string[]) : [],
              );
          });
          return true;
        case "permission_changed": {
          const value = d as { session_id?: string; mode?: unknown; version?: unknown; active_branch_id?: unknown };
          if (value?.session_id) {
            const store = useSessionStore.getState();
            if (!sameSettingsBranch(store.composerSettingsBySession[value.session_id] ?? {}, value)) return true;
            const patch = permissionSnapshotPatch(store.composerSettingsBySession[value.session_id] ?? {}, value);
            if (patch) store.setComposerSettings(patch, value.session_id);
          }
          return true;
        }
        case "sandbox_changed":
          import("@/lib/session-store").then(({ useSessionStore }) => {
            const dd = (d || {}) as { session_id?: string; sandbox?: unknown; request_id?: string; active_branch_id?: unknown };
            // Correlated replies belong to their request owner, which rejects stale reads.
            if (dd.request_id) return;
            if (!dd.session_id || typeof dd.sandbox !== "boolean") return;
            if (!sameSettingsBranch(useSessionStore.getState().composerSettingsBySession[dd.session_id] ?? {}, dd)) return;
            useSessionStore
              .getState()
              .setComposerSettings({ sandbox: dd.sandbox }, dd.session_id);
          });
          return true;
        case "session_loaded":
          if (!socket || !d || !acceptSessionLoad(socket, d, loadSessionData)) return true;
          clearTimeout(historyDeadline);
          recovery?.start();
          notifyDesktopSessionLoaded((d as { id?: unknown } | null)?.id);
          {
            const dd = d as {
              id?: unknown;
              head_id?: unknown;
              messages?: unknown;
              turns?: Array<{ rows: Array<{ id?: unknown; status?: unknown; attachCards?: unknown[]; runtimeChildren?: unknown[] }> }>;
              graph?: unknown;
              run_active?: unknown;
              run_task?: { msg_id?: string; execution_id?: string; status_version?: number; op_kind?: string; op_id?: string } | null;
              run_seq?: unknown;
            } | undefined;
            if (typeof dd?.id === "string" && dd.id) {
              const runFresh = acceptRunFrame(dd.id, dd.run_seq);
              void import("@/lib/chat/send/send-queue").then((m) =>
                m.reconcileAfterSessionLoad(dd.id as string, dd.run_active === true, dd.run_task, runFresh),
              );
            }
          }
          // Restore the session's additional working directories from the
          // persisted settings (refresh / device switch recovery).
          {
            const dd = d as Record<string, unknown> | null;
            const sid = dd?.id;
            const settings = dd?.settings as
              | Record<string, unknown>
              | undefined;
            const dirs = settings?.additional_working_dirs;
            const permissionMode = settings?.permission_mode;
            if (typeof sid === "string" && sid) {
              import("@/lib/session-store").then(({ useSessionStore }) => {
                const store = useSessionStore.getState();
                if (Array.isArray(dirs)) {
                  store.setAdditionalWorkingDirs(sid, dirs as string[]);
                }
                if (typeof permissionMode === "string" && permissionMode) {
                  const patch = permissionSnapshotPatch(store.composerSettingsBySession[sid] ?? {}, {
                    mode: permissionMode, version: settings?.permission_version ?? 0,
                  });
                  if (patch) store.setComposerSettings(patch, sid);
                }
                if (typeof settings?.sandbox === "boolean") {
                  store.setComposerSettings(
                    { sandbox: settings.sandbox as boolean },
                    sid,
                  );
                }
              });
            }
          }
          // Pull the branch list for the freshly-loaded session. The DAG's
          // branch-name badges draw from _branchesByConv, which nothing
          // else fills on a plain load (only rename/delete events and the
          // Branches panel send list_branches) — without this the first
          // paint shows a nameless graph until some other interaction.
          {
            const sid = (d as Record<string, unknown>)?.id;
            if (typeof sid === "string" && sid) {
              socket?.send(
                JSON.stringify({ action: "list_branches", session_id: sid }),
              );
            }
          }
          // 刷新恢复：函数可能正阻塞在 runtime.ask 等用户答题。live 的
          // question.asked 帧在本次（重）连之前就发过了，刷新后丢了卡片 →
          // 函数卡在 Running。这里确定性地按 session 主动拉一次还在 pending
          // 的提问重建卡片（不靠 WS replay 时序）。
          {
            const sid = (d as Record<string, unknown>)?.id;
            if (typeof sid === "string" && sid) {
              const previousDecisions = useSessionStore.getState().pendingDecisions.filter((q) => q.sessionId === sid);
              void fetch(`/api/questions?session_id=${encodeURIComponent(sid)}`)
                .then((r) => (r.ok ? r.json() as Promise<{ questions?: unknown }> : null))
                .then((j) => {
                  if (!j || !Array.isArray(j.questions)) return;
                  const qs = j.questions.filter(
                    (q): q is Record<string, unknown> => !!q && typeof q === "object",
                  );
                  for (const request of pendingExecutionReplayRequests(qs)) {
                    recovery?.request(request.execution_id, request.after_sequence);
                  }
                  import("@/lib/session-store").then(({ useSessionStore }) => {
                    const store = useSessionStore.getState();
                    const openIds = new Set(qs.map((q: Record<string, unknown>) => String(q.id)));
                    for (const prior of previousDecisions) {
                      if (!openIds.has(prior.id) && store.pendingDecisions.includes(prior)) store.dequeueDecision(prior.id);
                    }
                    for (const dd of qs as Record<string, unknown>[]) {
                      if (!dd.id) continue;
                      store.enqueueDecision({
                        id: String(dd.id),
                        sessionId: String(dd.session_id || sid),
                        executionId: String(dd.execution_id || ""),
                        waitGeneration: Number(dd.wait_generation || 0),
                        expectedVersion: Number(dd.expected_version || 0),
                        kind: (dd.kind as PendingDecision["kind"]) || "ask",
                        prompt: String(dd.prompt || ""),
                        options: Array.isArray(dd.options) ? (dd.options as string[]) : [],
                        multi: Boolean(dd.multi),
                        allow_custom: dd.allow_custom !== false,
                        detail: dd.detail ? String(dd.detail) : undefined,
                        tool: dd.tool ? String(dd.tool) : undefined,
                        args: (dd.args as Record<string, unknown>) || undefined,
              allowedScopes: Array.isArray(dd.allowed_scopes) ? dd.allowed_scopes.filter((scope): scope is string => typeof scope === "string") : undefined,
              risk_level: (dd.risk_level as "low" | "medium" | "high") || undefined,
              askedBy: typeof dd.asked_by === "string" && dd.asked_by ? dd.asked_by : undefined,
              reason: typeof dd.reason === "string" && dd.reason ? dd.reason : undefined,
              writeRoot: typeof dd.write_root === "string" && dd.write_root ? dd.write_root : undefined,
                        schema:
                          dd.schema && typeof dd.schema === "object"
                            ? (dd.schema as PendingDecision["schema"])
                            : undefined,
                        questions: Array.isArray(dd.questions)
                          ? (dd.questions as PendingDecision["questions"])
                          : undefined,
                      });
                    }
                  });
                })
                .catch(() => { /* 网络抖动忽略；WS replay 仍是兜底 */ });
            }
          }
          return true;
        case "run_state": {
          applyRunState(d as RunStateData | undefined);
          return true;
        }
        case "running_sessions": {
          // The answer to this connection's `get_running_sessions`
          // (lib/chat/run/running-sessions.ts).
          const listed = listedRunStates(d);
          for (const state of listed) applyRunState(state);
          void import("@/lib/chat/run/other-branch-runs").then(({ useOtherBranchRuns, sessionsWithBranchRun }) => {
            const shown = [
              ...Object.keys(useSessionStore.getState().runningTasks),
              ...Object.keys(sessionsWithBranchRun(useOtherBranchRuns.getState().runs)),
            ];
            for (const sid of unlistedRunning(listed, shown, runtimeState.currentSessionId)) {
              socket?.send(JSON.stringify({ action: "get_run_state", session_id: sid }));
            }
          });
          return true;
        }
        case "sessions_list":
          handleSessionsList((d ?? []) as never);
          return true;
        case "always_on_report": {
          const report = d?.report as { id?: unknown; session_id?: unknown; title?: unknown } | undefined;
          const sid = report?.session_id;
          // The server decides from the Agent's notify rules whether this
          // kind of report raises a desktop notification (lib/desktop/attention).
          // The desktop App says it took the notification; without that answer
          // the worker posts it itself.
          if (typeof sid === "string" && sid && typeof report?.id === "string") {
            const reportId = report.id;
            void import("@/lib/desktop/attention").then(({ reportAttention, reportNotifiesDesktop }) => {
              if (!reportNotifiesDesktop(d)) return;
              const shown = reportAttention({
                kind: "report", sessionId: sid, key: `always-on-report:${reportId}`,
                title: typeof d?.agent_name === "string" ? d.agent_name : undefined,
                detail: typeof report.title === "string" ? report.title : undefined,
              });
              if (shown) socket?.send(JSON.stringify({ action: "reminder_shown", id: reportId }));
            });
          }
          if (typeof sid === "string" && sid) {
            void import("@/lib/chat/conversation-work").then(({ notifyConversationWorkChanged }) => notifyConversationWorkChanged(sid));
          }
          return true;
        }
        case "reminder": {
          // A scheduled reminder came due (openprogram/runtime/scheduler/reminders.py).
          const reminderId = typeof d?.id === "string" ? d.id : "";
          const message = typeof d?.message === "string" ? d.message : "";
          if (reminderId && message) {
            const sid = typeof d?.session_id === "string" ? d.session_id : "";
            void import("@/lib/format-utils/toast").then(({ showToast }) =>
              showToast(`⏰ ${message}`, { duration: 15000 }));
            // The desktop App posts the system notification and says so;
            // without that answer the worker posts it itself.
            void import("@/lib/desktop/attention").then(({ reportAttention }) => {
              const shown = reportAttention({
                kind: "reminder", sessionId: sid, key: `reminder:${reminderId}`,
                title: translateText("Reminder", "提醒"), detail: message,
              });
              if (shown) socket?.send(JSON.stringify({ action: "reminder_shown", id: reminderId }));
            });
          }
          return true;
        }
        case "memory_writer_failed": {
          // The background memory writer failed in a way its own repairs and
          // retries do not cover (openprogram/state/memory/runtime/writer_status.py);
          // said once per streak.
          const reason = typeof d?.reason_code === "string" ? d.reason_code : "";
          void import("@/components/memory/writer-health").then(({ writerFailureText }) =>
            import("@/lib/format-utils/toast").then(({ showToast }) => showToast(
              translateText("Conversations are not reaching memory: ", "对话没有写进记忆：") + writerFailureText(reason),
              { tone: "error", duration: 12000, link: { label: translateText("Open Memory", "打开记忆"), href: "/memory" } },
            )));
          return true;
        }
        case "always_on_update":
        case "open_work_update": {
          // A conversation's wake, report delivery or open work changed
          // (openprogram/runtime/conversation/always_on, openprogram/runtime/conversation/open_work.py).
          const sid = d?.session_id;
          if (typeof sid === "string" && sid) {
            void import("@/lib/chat/conversation-work").then(({ notifyConversationWorkChanged }) => notifyConversationWorkChanged(sid));
          }
          return true;
        }
        case "branch_running_task":
        case "branch_running_task_clear": {
          // Another branch's run, kept for the panes that show a different
          // branch of the conversation (lib/chat/run/other-branch-runs.ts).
          const type = msg.type;
          if (d && typeof d === "object") {
            void import("@/lib/chat/run/other-branch-runs").then(({ useOtherBranchRuns }) => {
              useOtherBranchRuns.getState().applyFrame(type, d as Record<string, unknown>);
            });
          }
          return true;
        }
        case "todos_update": {
          // A task changed; `changed` holds its segment under the reply
          // that changed it (lib/chat/transcript/task-blocks.ts).
          const sid = d?.session_id;
          if (typeof sid === "string" && sid) {
            const changed = d?.changed;
            void import("@/lib/chat/transcript/task-blocks").then(({ useTaskStore }) => {
              useTaskStore.getState().upsert(sid, changed);
            });
          }
          return true;
        }
        case "projects_changed":
          window.dispatchEvent(new Event("project-changed"));
          return true;
        case "frontend_build":
          // Static import: after a swap this page's lazy chunks no longer exist.
          noteFrontendBuild(d?.id);
          return true;
        case "session_updated":
          handleSessionUpdated((d ?? null) as never);
          return true;
        case "session_deleted": {
          // Broadcast by ws_actions/session/lifecycle.py::handle_delete_session so
          // every OTHER tab drops the row too (the deleting tab already
          // removed it optimistically in sessions-list.tsx). session_id
          // sits at the frame top level, not inside `data`.
          // Its tabs close and focus moves on exactly as in the deleting
          // window (a repeat for the deleting window itself is a no-op).
          const sid = (msg as { session_id?: string }).session_id;
          if (sid) {
            delete runtimeState.conversations[sid];
            void import("@/lib/tabs/navigation/open-session").then(({ forgetSessionAndRefocus }) => {
              forgetSessionAndRefocus(sid);
            });
          }
          return true;
        }
        case "session_channel_updated": {
          const sid = d?.session_id as string | undefined;
          const conv = sid ? runtimeState.conversations[sid] : undefined;
          if (d?.ok && conv) {
            conv.channel = (d.channel as string) || null;
            conv.account_id = (d.account_id as string) || null;
            conv.peer = (d.peer as string) || null;
            mirrorUpsertConv(conv as Record<string, unknown>);
            if (sid === runtimeState.currentSessionId) {
              refreshStatusSource();
              refreshChannelBadge();
            }
          }
          return true;
        }
        // Catch-all: surface any unhandled backend message as a window
        // event so component-level listeners (project menu, settings
        // panel, etc.) can pick them up without needing
        // a dedicated case here. This replaces the legacy
        // window.handleMessage fallback.
        default:
          try {
            window.dispatchEvent(
              new CustomEvent("op:ws-message", {
                detail: { type: msg.type, data: d },
              }),
            );
          } catch {
            /* defensive */
          }
          return true;
      }
    }

    function connect(): void {
      void connectAuthenticated();
    }

    async function connectAuthenticated(): Promise<void> {
      const generation = ++connectGeneration;
      if (stopped) return;
      try {
        await waitForOwnerAuthBootstrap();
        if (stopped || generation !== connectGeneration) return;
        const probe = await fetch("/api/auth/session", {
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
        });
        if (probe.status === 401) {
          const recovered = await recoverOwnerAuth();
          if (stopped || generation !== connectGeneration) return;
          if (!recovered) {
            updateStatus("disconnected");
            if (mobileBridge()) {
              // Reachable, and the credential refused: the device was
              // revoked. The app takes the user back to pairing.
              callMobile("signedOut");
              return;
            }
            if (!authNoticeShown) {
              authNoticeShown = true;
              // The worker mints a new owner token each time it starts. The
              // App fetches it again; a plain browser needs a new link.
              const inApp = typeof window.openprogramDesktop?.refreshOwnerAuth === "function";
              showToast(inApp
                ? translateText(
                  "Owner authentication expired. Reopen OpenProgram to continue.",
                  "所有者认证已过期。请重新打开 OpenProgram 后再继续。",
                )
                : translateText(
                  "This page isn't signed in, so it can't load providers, models or agents. Run `openprogram web` in a terminal to open it signed in (`openprogram dev open` for the development instance).",
                  "这个页面没有登录，读不到 Provider、模型和 Agent。在终端运行 `openprogram web`，会打开已登录的页面（开发版用 `openprogram dev open`）。",
                ), { tone: "error", duration: 20000 });
            }
            if (!stopped) reconnectTimer = setTimeout(connect, 5000);
            return;
          }
        }
      } catch {
        /* Probe failure is not fatal; the WebSocket handshake still reports auth. */
      }
      if (stopped || generation !== connectGeneration) return;
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const historyFragments = createHistoryFragmentDecoder();
      let replayingHistory = false;
      // In the phone app the page is not on the computer's origin: the app
      // names the socket's address and its credential subprotocol.
      const mobile = mobileBridge();
      socket = mobile
        ? new WebSocket(mobile.socket.url, mobile.socket.protocols)
        : new WebSocket(proto + "//" + location.host + "/ws");
      const connection = socket;
      recovery?.dispose();
      const connectionRecovery = createExecutionRecovery({
        send: request => {
          if (connection.readyState !== WebSocket.OPEN) throw new Error("Connection closed");
          connection.send(JSON.stringify(request));
        },
        schedule: (callback, delay) => setTimeout(callback, delay),
        cancel: timer => clearTimeout(timer),
      });
      recovery = connectionRecovery;
      setSocket(socket);
      pushStatusBadge();

      socket.onopen = () => {
        if (socket !== connection || stopped) return;
        socket?.send(JSON.stringify({ action: "framework_interface_register", window_id: interfaceWindowId() }));
        updateStatus("connected");
        window.dispatchEvent(new CustomEvent("op:browser-connection", { detail: { connected: true } }));
        if (reconnectTimer) {
          clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        // currentSessionId is derived from the URL by state.js / the
        // app-shell route effect — send agent_settings + the initial
        // session load so badges + transcript reflect the right conv.
        loadAgentSettings();
        // Changes told while the socket was down were lost: every topic
        // a window listens to reads again.
        dispatchReconnected();
        // A catalogue change may have happened while this window was
        // disconnected. Re-read active model queries on every reconnect.
        getQueryClient()?.invalidateQueries({ queryKey: ["models-enabled"] });
        getQueryClient()?.invalidateQueries({ queryKey: ["providers"] });
        window.dispatchEvent(new CustomEvent("op:provider-models-changed", {
          detail: { provider: "" },
        }));
        const desktopWindowId = (
          window as unknown as { openprogramDesktop?: { windowId?: string } }
        ).openprogramDesktop?.windowId;
        if (desktopWindowId) {
          socket?.send(JSON.stringify({
            action: "webtab_register", window_id: desktopWindowId,
          }));
        }
        // Before the focus below: a fresh socket counts as visible, and a
        // reconnect in a background window must not read the open chat.
        socket?.send(JSON.stringify({
          action: "set_window_hidden", hidden: document.visibilityState === "hidden",
        }));
        socket?.send(JSON.stringify({ action: "list_sessions", history_version: HISTORY_PROTOCOL }));
        // Turns already going announced themselves before this socket
        // opened: ask which conversations run (the `running_sessions` case).
        socket?.send(JSON.stringify({ action: "get_running_sessions" }));
        // Machine changes broadcast while disconnected were missed;
        // the reply lands in the `machines` case below.
        socket?.send(JSON.stringify({ action: "list_machines" }));
        if (runtimeState.currentSessionId) {
          clearTimeout(historyDeadline);
          historyDeadline = setTimeout(() => connectionRecovery.start(), 15_000);
          requestSessionLoad({
              action: "load_session",
              session_id: runtimeState.currentSessionId,
            });
          // Re-establish "viewing this conv" focus + clear any unread (blue
          // status dot) that accrued while the socket was disconnected.
          socket?.send(
            JSON.stringify({
              action: "mark_session_read",
              session_id: runtimeState.currentSessionId,
            }),
          );
        }
        for (const cursor of loadExecutionCursors()) {
          connectionRecovery.request(cursor.execution_id, cursor.next_sequence - 1);
        }
        // The first transcript unblocks recovery. An empty/new chat has no
        // transcript request to wait for.
        if (!runtimeState.currentSessionId) connectionRecovery.start();
        // A queue item whose socket write failed is retained in renderer
        // memory. Query background sessions without load_session: loading a
        // transcript also changes this socket's focused-session marker.
        void import("@/lib/chat/send/send-queue").then((m) => {
          const queued = Object.keys(m.useSendQueue.getState().queues);
          const focused = runtimeState.currentSessionId;
          for (const sid of new Set(queued.filter((id) => id !== focused))) {
            socket?.send(JSON.stringify({ action: "get_run_state", session_id: sid }));
          }
        });
      };

      socket.onmessage = (e) => {
        if (socket !== connection || stopped) return;
        heardAt = Date.now();
        try {
          const msg = JSON.parse(e.data) as {
            type?: string;
            data?: { session_id?: string };
          };
          if (replayingHistory) { dispatch(msg); return; }
          const ready = historyFragments.receive(e.data);
          if (ready.length === 1 && ready[0] === e.data && msg.type !== "history_fragment") {
            dispatch(msg);
            return;
          }
          replayingHistory = true;
          try {
            for (const complete of ready) connection.dispatchEvent(new MessageEvent("message", { data: complete }));
          } finally { replayingHistory = false; }
        } catch (err) {
          console.error("[useWS] onmessage parse error:", err);
        }
      };

      socket.onclose = () => {
        connectionRecovery.dispose();
        disposeSessionLoads(connection);
        historyFragments.clear();
        if (socket !== connection || stopped) return;
        flushTextFrames();
        clearSessionByMsgId();
        settleSendsOnDisconnect();
        clearTimeout(historyDeadline);
        updateStatus("disconnected");
        window.dispatchEvent(new CustomEvent("op:browser-connection", { detail: { connected: false } }));
        if (!stopped) reconnectTimer = setTimeout(connect, reconnectNow ? 0 : 2000);
        reconnectNow = false;
      };

      socket.onerror = () => connection.close();
    }

    async function start(): Promise<void> {
      try {
        await waitForOwnerAuthBootstrap();
      } catch {
        return;
      }
      if (stopped) return;
      initChatPage();
      // The phone app shows the list it kept until the computer's arrives.
      const kept = cachedSessionList<never>();
      if (kept && Object.keys(runtimeState.conversations).length === 0) handleSessionsList(kept);
      connect();
    }
    start();

    const keepalive = setInterval(() => {
      if (socket && socket.readyState === WebSocket.OPEN) socket.send("ping");
    }, 30000);

    // A phone suspends the page in the background and the socket dies
    // without a close, so coming back would wait for the next keepalive.
    // On return (and when the app finds another address) a socket that is
    // not open reconnects at once, and an open one must answer a ping
    // within 2s or is closed and replaced.
    // A dead connection's close can take a minute to report: its close
    // handling runs now, and the next socket opens at once.
    const drop = (dead: WebSocket) => {
      const closed = dead.onclose;
      dead.onclose = null;
      reconnectNow = true;
      try { dead.close(); } catch { /* already closed */ }
      closed?.call(dead, new CloseEvent("close"));
    };
    const wake = () => {
      if (stopped || document.visibilityState === "hidden") return;
      const mobile = mobileBridge();
      const moved = !!(mobile && socket && socket.url !== mobile.socket.url);
      if (!socket || socket.readyState === WebSocket.CLOSED || socket.readyState === WebSocket.CLOSING) {
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = null;
        connect();
        return;
      }
      if (socket.readyState !== WebSocket.OPEN && !moved) return;
      const probed = socket;
      if (moved) {
        drop(probed);
        return;
      }
      const sent = Date.now();
      try { probed.send("ping"); } catch { /* closing already */ }
      clearTimeout(wakeCheck);
      wakeCheck = setTimeout(() => {
        if (socket === probed && heardAt < sent) drop(probed);
      }, 2000);
    };
    document.addEventListener?.("visibilitychange", wake);
    window.addEventListener?.("online", wake);
    window.addEventListener?.("openprogram-mobile-address", wake);

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      clearInterval(keepalive);
      clearTimeout(wakeCheck);
      document.removeEventListener?.("visibilitychange", wake);
      window.removeEventListener?.("online", wake);
      window.removeEventListener?.("openprogram-mobile-address", wake);
      recovery?.dispose();
      clearTimeout(historyDeadline);
      if (socket) {
        disposeSessionLoads(socket);
        socket.onclose = null;
        socket.close();
      }
      if (runtimeState.ws === socket) {
        flushTextFrames();
        clearSessionByMsgId();
        window.dispatchEvent(new CustomEvent("op:browser-connection", { detail: { connected: false } }));
        setSocket(null);
        pushStatusBadge();
      }
    };
  }, []);
}
