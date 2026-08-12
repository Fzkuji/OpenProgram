/**
 * Typed payloads for the `op:*` window CustomEvents that `use-ws.ts`
 * dispatches when a backend WS frame arrives.
 *
 * One declaration serves both sides: the `WindowEventMap` augmentation at
 * the bottom makes `window.dispatchEvent` reject a mistyped detail AND
 * makes `window.addEventListener("op:task-status", h)` infer `h`'s
 * argument, so neither the dispatcher nor the listeners need
 * `as CustomEvent<...>` / `as EventListener` casts.
 *
 * Each interface mirrors the `data` dict its Python broadcaster builds —
 * the source of truth is cited above each one. Fields stay optional
 * because the payload arrives as untrusted JSON off the socket.
 */

/** `openprogram/webui/ws_actions/session.py:_broadcast_permission_rules` */
export interface PermissionRulesDetail {
  project_id?: string;
  allow?: string[];
  deny?: string[];
  ask?: string[];
}

/** `openprogram/agent/task/runner.py:_broadcast_task_status` */
export interface TaskStatusDetail {
  task_id?: string;
  session_id?: string;
  status?: string;
  parent_msg_id?: string | null;
  target_branch_head_id?: string | null;
  head_id?: string | null;
  label?: string | null;
  subject?: string | null;
  error?: string | null;
  created_at?: number | string | null;
  started_at?: number | string | null;
  completed_at?: number | string | null;
  resource?: Record<string, unknown> | null;
}

/**
 * Reply-envelope shape shared by `op:task-message` and the
 * `op:ws-message` catch-all: the original frame's `type` plus its
 * `data` dict, re-emitted so the panel that issued the request can
 * correlate the response.
 */
export interface WsEnvelopeDetail<T = Record<string, unknown>> {
  type?: string;
  data?: T;
}

declare global {
  interface WindowEventMap {
    "op:permission-rules": CustomEvent<PermissionRulesDetail>;
    "op:task-status": CustomEvent<TaskStatusDetail>;
    "op:task-message": CustomEvent<WsEnvelopeDetail>;
    "op:ws-message": CustomEvent<WsEnvelopeDetail>;
  }
}

export {};
