"use client";

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

import type {
  PermissionRulesDetail,
  JobStatusDetail,
} from "@/lib/net/ws-events";
import type { PendingDecision } from "@/lib/session-store";
import {
  loadSessionData,
  onBranchCheckedOut,
  onWorkspaceAlignmentResolved,
  onBranchesListMessage,
  onChannelAccountsMessage,
} from "@/lib/runtime-bridge/conversations";
import {
  clearHydratedTreePaths,
  handleRunningTask,
  handleRunningTaskClear,
  handleSessionsList,
  handleSessionUpdated,
  initChatPage,
  wsHandleChatAck,
  wsHandleChatResponse,
  wsHandleStatus,
} from "@/lib/runtime-bridge/chat-handlers";
import { mirrorUpsertConv } from "@/lib/runtime-bridge/conv-store-mirror";
import { runtimeState, setSocket } from "@/lib/runtime-bridge/state";
import { applyChatWsMessage, clearSessionByMsgId } from "@/lib/net/chat-stream";
import { waitForOwnerAuthBootstrap } from "@/lib/net/owner-auth-bootstrap";
import { translateText } from "@/lib/i18n";
import { getQueryClient } from "@/lib/query-client";
import {
  loadAgentSettings,
  loadProviders,
  updateAgentBadges,
  updateProviderBadge,
} from "@/lib/runtime-bridge/providers";
import { addSystemMessage, formatProviderLabel } from "@/lib/runtime-bridge/helpers";
import {
  loadProgramsMeta,
  renderFunctions,
} from "@/lib/runtime-bridge/functions-panel";
import { refreshStatusSource, updateStatus } from "@/lib/runtime-bridge/ui";
import { refreshChannelBadge } from "@/lib/runtime-bridge/conversations";

export function useWS(): void {
  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;

    /** React-side dispatch for all WS message types. Known types have
     *  explicit handlers; unknown types are surfaced as `op:ws-message`
     *  window events for component-level listeners. */
    function dispatch(msg: {
      type?: string;
      data?: Record<string, unknown>;
    }): boolean {
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
        case "session_reload": {
          const sid = d?.session_id as string | undefined;
          if (sid && sid === runtimeState.currentSessionId) {
            socket?.send(
              JSON.stringify({ action: "load_session", session_id: sid }),
            );
          }
          return true;
        }
        case "branch_message": {
          // Branch-to-branch communication: show a line in the sender's
          // chat stream. kind: "sent" (我发给X) | "replied" (X回复了).
          const sid = d?.session_id as string | undefined;
          if (sid && sid === runtimeState.currentSessionId) {
            const kind = (d?.kind as string) || "sent";
            const peer = (d?.peer as string) || "?";
            const summary = (d?.summary as string) || "";
            const label =
              kind === "replied"
                ? `📥 分支 ${peer} 回复了：${summary}`
                : `📤 已发消息给分支 ${peer}：${summary}`;
            import("@/lib/session-store").then(({ useSessionStore }) => {
              useSessionStore.getState().appendMessage(sid, {
                id: "branchmsg_" + Math.random().toString(36).slice(2, 10),
                role: "system",
                content: label,
                source: "branch_message",
              } as unknown as Parameters<
                ReturnType<typeof useSessionStore.getState>["appendMessage"]
              >[1]);
            }).catch(() => { /* best-effort UI line */ });
          }
          return true;
        }
        case "rewind_points": {
          // `/rewind` with no argument asks for the list; the user then
          // types `/rewind N`. Render it as a numbered system line in the
          // transcript — same append path as `branch_message` — so the
          // indices stay on screen while they type the follow-up.
          const sid = d?.session_id as string | undefined;
          if (sid && sid !== runtimeState.currentSessionId) return true;
          const target = sid ?? runtimeState.currentSessionId;
          if (!target) return true;
          const err = d?.error as string | undefined;
          const points = (d?.points as Array<Record<string, unknown>>) ?? [];
          const body = err
            ? `无法列出回退点：${err}`
            : points.length === 0
              ? "没有可回退的对话轮次。"
              : "回退点（用 /rewind N 选择）：\n"
                + points
                    .map((p, i) => {
                      const files = (p.files_affected as string[]) ?? [];
                      const tail = files.length
                        ? `  [${files.length} 个文件]`
                        : "";
                      return `${i + 1}. ${(p.summary as string) || "(空)"}${tail}`;
                    })
                    .join("\n");
          void import("@/lib/session-store").then(({ useSessionStore }) => {
            useSessionStore.getState().appendMessage(target, {
              id: "rewindpts_" + Math.random().toString(36).slice(2, 10),
              role: "system",
              content: body,
              source: "rewind_points",
            } as unknown as Parameters<
              ReturnType<typeof useSessionStore.getState>["appendMessage"]
            >[1]);
          }).catch(() => { /* best-effort UI line */ });
          return true;
        }
        case "action_error": {
          // The backend had no handler for an action we sent. Always a
          // frontend/backend contract break, never a user error — say so
          // loudly instead of leaving the caller waiting on a frame that
          // will never arrive.
          const act = d?.action as string | undefined;
          console.error("[useWS] backend rejected action:", act, d?.error);
          void import("@/lib/format-utils/toast").then(({ showToast }) => {
            showToast(
              translateText(
                `Unknown action ${act ?? "?"} — no backend handler`,
                `未知操作 ${act ?? "?"} — 后端没有对应处理器`,
              ),
              { tone: "error" },
            );
          });
          return true;
        }
        case "attach_branch_result": {
          // Failure surface for the Branches panel's "Attach to" action.
          // On success (including duplicate re-attach) the backend
          // broadcasts `session_reload` for the anchor session and the
          // load_session → session_loaded chain redraws the attach card
          // and branch list (ws_actions/branch.py::handle_attach_branch),
          // so only the failure branch needs handling here.
          if (d?.ok !== false) return true;
          // Same ownership rule as the rewind_result consumer: a frame
          // owned by another conversation must not toast into this one.
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
          // successful merge broadcasts `session_reload` (reason "merge")
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
        case "skills:changed": {
          // File-system watcher fired — refresh the skills list so the
          // /skills page, Discovery counts, and slash menu reflect the
          // change without any user action.
          import("@/lib/state/skills-store").then(({ useSkills }) => {
            useSkills.getState().fetchSkills();
          });
          return true;
        }
        case "plugins:changed":
        case "plugins:update_available":
          // Both mean "the plugins list is stale" — update_available is
          // broadcast by the server's update poll (server.py) and rides
          // the same refresh so the upgrade hint can surface.
          import("@/lib/state/plugins-store").then(({ usePluginsStore }) => {
            usePluginsStore.getState().refresh();
          });
          return true;
        case "programs:changed":
          // A harness was installed at runtime (cloned into agentics/ or
          // `programs install`) and the backend re-scanned — refresh the
          // function catalogue so its new functions show up live, no
          // reload needed. Same shape as skills/plugins above.
          import("@/lib/state/functions-actions").then(({ refreshFunctionsList }) => {
            refreshFunctionsList();
          });
          return true;
        case "execution.updated": {
          const execution = (msg as { execution?: {
            execution_id?: string;
            session_id?: string;
            status?: string;
            reason_code?: string;
          } }).execution || d;
          if (!execution?.execution_id) return true;
          import("@/lib/session-store").then(({ useSessionStore }) => {
            const store = useSessionStore.getState();
            const sid = String(execution.session_id || "");
            const eid = String(execution.execution_id);
            if (sid) {
              const current = store.messagesById[eid];
              // 终态不可回退：stopSession 已乐观把消息标 cancelled，服务端
              // 随后广播的 cancelling（宽限期中间态）不能把它拉回"运行中"，
              // 否则气泡会重新显示思考中（turn-occupancy.md）。
              const terminal = new Set(
                ["cancelled", "completed", "failed", "interrupted", "error", "done"],
              );
              if (current && !(terminal.has(String(current.status)) && !terminal.has(String(execution.status)))) {
                store.updateMessage(sid, eid, {
                  status: execution.status as never,
                });
              }
            }
            const task = sid ? store.runningTasks[sid] : undefined;
            const matches = Boolean(
              task && (
                task.execution_id === eid
                || (task.msg_id && `${task.msg_id}_reply` === eid)
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
          });
          return true;
        }
        case "running_task":
          handleRunningTask(d);
          return true;
        case "running_task_clear":
          handleRunningTaskClear(
            (d as { session_id?: string } | undefined)?.session_id,
            {
              execution_id: (d as { execution_id?: string } | undefined)?.execution_id,
              msg_id: (d as { msg_id?: string } | undefined)?.msg_id,
            },
          );
          return true;
        case "job_status": {
          // Async job lifecycle broadcast. Dispatch a
          // window event so any panel listening (BranchesPanel,
          // JobsPanel) can update without prop-drilling. The
          // existing session_reload broadcast picks up DAG/attach
          // changes; this event is purely for the in-flight badge.
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
        case "cancel_job_result": {
          // Replies to the four job WS actions. We let the
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
          loadProviders();
          if (msg.type === "provider_changed") {
            addSystemMessage(
              "Switched to " + formatProviderLabel(d as never),
            );
          }
          return true;
        case "agent_settings_changed": {
          // Keep window._agentSettings in sync (backward compat)
          const as = runtimeState._agentSettings;
          if (as) {
            if (d?.chat) as.chat = d.chat as Record<string, unknown>;
            if (d?.exec) as.exec = d.exec as Record<string, unknown>;
          }
          // Push directly to React store (primary data source)
          import("@/lib/session-store").then(({ useSessionStore }) => {
            const chatData = d?.chat as Record<string, unknown> | undefined;
            const execData = d?.exec as Record<string, unknown> | undefined;
            const chatValid = chatData?.provider && chatData?.model;
            const execValid = execData?.provider && execData?.model;
            useSessionStore.getState().setAgentSettings({
              chat: chatValid ? chatData : undefined,
              exec: execValid ? execData : undefined,
            });
          });
          // Still fetch full settings (includes thinking config etc.)
          loadAgentSettings();
          // Enabled-models may have changed with the settings (Settings
          // toggles broadcast this event) — drop the query cache so every
          // tab's model picker refetches, not just the settings tab.
          getQueryClient()?.invalidateQueries({ queryKey: ["models-enabled"] });
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
        // （docs/design/ui/composer-interaction-modes.md）。不再走独立浮窗。
        case "question.asked":
          import("@/lib/session-store").then(({ useSessionStore }) => {
            const dd = (d || {}) as Record<string, unknown>;
            if (!dd.id) return;
            useSessionStore.getState().enqueueDecision({
              id: String(dd.id),
              sessionId: String(dd.session_id || ""),
              kind: (dd.kind as "ask" | "confirm" | "approval" | "form" | "ask_many") || "ask",
              prompt: String(dd.prompt || ""),
              options: Array.isArray(dd.options) ? (dd.options as string[]) : [],
              multi: Boolean(dd.multi),
              allow_custom: dd.allow_custom !== false,
              detail: dd.detail ? String(dd.detail) : undefined,
              tool: dd.tool ? String(dd.tool) : undefined,
              args: (dd.args as Record<string, unknown>) || undefined,
              risk_level: (dd.risk_level as "low" | "medium" | "high") || undefined,
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
          runtimeState.availableFunctions = (d || []) as unknown[];
          import("@/lib/state/functions-store").then(({ useFunctions }) => {
            useFunctions.getState().setFunctions((d || []) as never[]);
          });
          loadProgramsMeta().then(() => renderFunctions());
          return true;
        case "channel_accounts":
          onChannelAccountsMessage(d as never);
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
        case "sandbox_changed":
          import("@/lib/session-store").then(({ useSessionStore }) => {
            const dd = (d || {}) as { session_id?: string; sandbox?: unknown };
            if (!dd.session_id || typeof dd.sandbox !== "boolean") return;
            useSessionStore
              .getState()
              .setComposerSettings({ sandbox: dd.sandbox }, dd.session_id);
          });
          return true;
        case "session_loaded":
          // A fresh transcript invalidates the per-run hydrate dedup —
          // see clearHydratedTreePaths for why this is the drain point.
          clearHydratedTreePaths();
          // Same drain point for the msg_id → session map: entries whose
          // terminal frame (result/error/cancelled) got lost would
          // otherwise sit in the module-level Map forever.
          clearSessionByMsgId();
          loadSessionData(d as never);
          {
            const dd = d as { id?: unknown; run_active?: unknown } | undefined;
            if (typeof dd?.id === "string" && dd.id) {
              void import("@/lib/state/send-queue").then((m) =>
                m.reconcileAfterSessionLoad(dd.id as string, dd.run_active === true),
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
                  store.setComposerSettings(
                    { effective_permission: permissionMode },
                    sid,
                  );
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
              void fetch(`/api/questions?session_id=${encodeURIComponent(sid)}`)
                .then((r) => (r.ok ? r.json() : null))
                .then((j) => {
                  const qs = j && Array.isArray(j.questions) ? j.questions : [];
                  import("@/lib/session-store").then(({ useSessionStore }) => {
                    const store = useSessionStore.getState();
                    for (const dd of qs as Record<string, unknown>[]) {
                      if (!dd.id) continue;
                      store.enqueueDecision({
                        id: String(dd.id),
                        sessionId: String(dd.session_id || sid),
                        kind: (dd.kind as PendingDecision["kind"]) || "ask",
                        prompt: String(dd.prompt || ""),
                        options: Array.isArray(dd.options) ? (dd.options as string[]) : [],
                        multi: Boolean(dd.multi),
                        allow_custom: dd.allow_custom !== false,
                        detail: dd.detail ? String(dd.detail) : undefined,
                        tool: dd.tool ? String(dd.tool) : undefined,
                        args: (dd.args as Record<string, unknown>) || undefined,
              risk_level: (dd.risk_level as "low" | "medium" | "high") || undefined,
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
          const dd = d as
            | { session_id?: unknown; run_active?: unknown }
            | undefined;
          if (typeof dd?.session_id === "string" && dd.session_id) {
            void import("@/lib/state/send-queue").then((m) =>
              m.reconcileAfterSessionLoad(
                dd.session_id as string,
                dd.run_active === true,
              ),
            );
          }
          return true;
        }
        case "sessions_list":
          handleSessionsList((d ?? []) as never);
          return true;
        case "session_updated":
          handleSessionUpdated((d ?? null) as never);
          return true;
        case "session_deleted": {
          // Broadcast by ws_actions/session.py::handle_delete_session so
          // every OTHER tab drops the row too (the deleting tab already
          // removed it optimistically in sessions-list.tsx). session_id
          // sits at the frame top level, not inside `data`.
          const sid = (msg as { session_id?: string }).session_id;
          if (sid) {
            delete runtimeState.conversations[sid];
            void import("@/lib/session-store").then(({ useSessionStore }) => {
              useSessionStore.getState().removeConversation(sid);
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
        // panel, rewind button, etc.) can pick them up without needing
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
      if (stopped) return;
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(proto + "//" + location.host + "/ws");
      setSocket(socket);

      socket.onopen = () => {
        updateStatus("connected");
        if (reconnectTimer) {
          clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        // currentSessionId is derived from the URL by state.js / the
        // app-shell route effect — send agent_settings + the initial
        // session load so badges + transcript reflect the right conv.
        loadAgentSettings();
        const desktopWindowId = (
          window as unknown as { openprogramDesktop?: { windowId?: string } }
        ).openprogramDesktop?.windowId;
        if (desktopWindowId) {
          socket?.send(JSON.stringify({
            action: "webtab_register", window_id: desktopWindowId,
          }));
        }
        socket?.send(JSON.stringify({ action: "list_sessions" }));
        if (runtimeState.currentSessionId) {
          socket?.send(
            JSON.stringify({
              action: "load_session",
              session_id: runtimeState.currentSessionId,
            }),
          );
          // Re-establish "viewing this conv" focus + clear any unread (blue
          // status dot) that accrued while the socket was disconnected.
          socket?.send(
            JSON.stringify({
              action: "mark_session_read",
              session_id: runtimeState.currentSessionId,
            }),
          );
        }
        // A queue item whose socket write failed is retained in renderer
        // memory. Query background sessions without load_session: loading a
        // transcript also changes this socket's focused-session marker.
        void import("@/lib/state/send-queue").then((m) => {
          const queued = Object.keys(m.useSendQueue.getState().queues);
          const focused = runtimeState.currentSessionId;
          for (const sid of new Set(queued.filter((id) => id !== focused))) {
            socket?.send(JSON.stringify({ action: "get_run_state", session_id: sid }));
          }
        });
      };

      socket.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data) as {
            type?: string;
            data?: { session_id?: string };
          };
          dispatch(msg);
        } catch (err) {
          console.error("[useWS] onmessage parse error:", err);
        }
      };

      socket.onclose = () => {
        updateStatus("disconnected");
        if (!stopped) reconnectTimer = setTimeout(connect, 2000);
      };

      socket.onerror = () => socket?.close();
    }

    async function start(): Promise<void> {
      try {
        await waitForOwnerAuthBootstrap();
      } catch {
        return;
      }
      if (stopped) return;
      initChatPage();
      connect();
    }
    start();

    const keepalive = setInterval(() => {
      if (socket && socket.readyState === WebSocket.OPEN) socket.send("ping");
    }, 30000);

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      clearInterval(keepalive);
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
      if (runtimeState.ws === socket) setSocket(null);
    };
  }, []);
}
