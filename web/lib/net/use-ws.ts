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
  TaskStatusDetail,
} from "@/lib/net/ws-events";
import type { PendingDecision } from "@/lib/session-store";
import {
  loadSessionData,
  onBranchCheckedOut,
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
import { translateText } from "@/lib/i18n";
import { externalLibsReady } from "@/lib/external-libs";
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
        case "steer_ack": {
          // queued=false means there was no live run to receive it — the
          // message is gone server-side. The composer already cleared on
          // send, so put the text back and say so; silently swallowing it
          // is how a typed course-correction vanishes with no trace.
          const sid = d?.session_id as string | undefined;
          if (!sid || d?.queued) return true;
          const text = (d?.message as string | undefined) ?? "";
          void import("@/lib/session-store").then(({ useSessionStore }) => {
            const store = useSessionStore.getState();
            if (text && !store.composerDrafts?.[sid]) {
              store.setComposerInputFor(sid, text);
            }
          });
          void import("@/lib/format-utils/toast").then(({ showToast }) => {
            showToast(
              translateText(
                "The run has ended — this message was not delivered",
                "任务已结束，这条消息没有送达",
              ),
              { tone: "error" },
            );
          });
          return true;
        }
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
        case "running_task":
          handleRunningTask(d);
          return true;
        case "running_task_clear":
          handleRunningTaskClear(
            (d as { session_id?: string } | undefined)?.session_id,
          );
          return true;
        case "task_status": {
          // Async task lifecycle broadcast (see
          // docs/design/runtime/async-task-lifecycle.md D9). Dispatch a
          // window event so any panel listening (BranchesPanel,
          // TasksPanel) can update without prop-drilling. The
          // existing session_reload broadcast picks up DAG/attach
          // changes; this event is purely for the in-flight badge.
          try {
            window.dispatchEvent(
              new CustomEvent("op:task-status", {
                detail: (d ?? {}) as TaskStatusDetail,
              }),
            );
          } catch {
            /* defensive: dispatchEvent should not throw */
          }
          return true;
        }
        case "spawn_task_result":
        case "tasks_list":
        case "task":
        case "cancel_task_result": {
          // Replies to the four task WS actions. We let the
          // requester correlate via the original send/await pattern
          // (no global handler needed). Surface as a window event
          // so a panel that did issue the request can match by
          // task_id if it wants to.
          try {
            window.dispatchEvent(
              new CustomEvent("op:task-message", {
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
        case "full_tree": // legacy no-op
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
        case "history_list": // legacy no-op
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
        case "session_loaded":
          // A fresh transcript invalidates the per-run hydrate dedup —
          // see clearHydratedTreePaths for why this is the drain point.
          clearHydratedTreePaths();
          // Same drain point for the msg_id → session map: entries whose
          // terminal frame (result/error/cancelled) got lost would
          // otherwise sit in the module-level Map forever.
          clearSessionByMsgId();
          loadSessionData(d as never);
          // Restore the session's additional working directories from the
          // persisted settings (refresh / device switch recovery).
          {
            const dd = d as Record<string, unknown> | null;
            const sid = dd?.id;
            const settings = dd?.settings as
              | Record<string, unknown>
              | undefined;
            const dirs = settings?.additional_working_dirs;
            if (typeof sid === "string" && sid && Array.isArray(dirs)) {
              import("@/lib/session-store").then(({ useSessionStore }) => {
                useSessionStore
                  .getState()
                  .setAdditionalWorkingDirs(sid, dirs as string[]);
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

    // Chat rendering needs the CDN libs (marked / KaTeX) on the page, so
    // wait for them before the first transcript paint. A load failure is
    // not fatal — connect anyway and let markdown fall back.
    async function start(): Promise<void> {
      try {
        await externalLibsReady();
      } catch {
        /* ignore — connect anyway */
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
