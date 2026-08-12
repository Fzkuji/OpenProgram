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
export type TaskResourceLimitName =
  | "max_live_per_session"
  | "max_queued_per_session"
  | "max_tasks_per_session"
  | "max_total_tokens"
  | "max_runtime_seconds"
  | "idle_timeout_seconds"
  | "max_cost_usd";

export interface TaskResourceView {
  task_id: string;
  status: string;
  resource_state: string;
  reason_code: string | null;
  reason_key: string | null;
  retryable: boolean;
  limits: {
    scheduler_capacity: number;
    limits: Record<TaskResourceLimitName, {
      configured: number | string | null;
      effective: number | string | null;
      source: string;
    }>;
  };
  capacity: {
    scheduler_capacity: number;
    session_live: { used: number; limit: number | null };
    session_queued: { used: number; limit: number | null };
    session_tasks: { used: number; limit: number | null };
    queue_position: number | null;
  };
  budget: {
    scope: string;
    tokens: {
      actual: number | null;
      reserved: number | null;
      limit: number | null;
    };
    cost_usd: {
      actual: string | null;
      reserved: string | null;
      limit: string | null;
      known: boolean | null;
      unknown_events: number | null;
    };
    runtime_seconds: { used: number | null; limit: number | null };
    idle_seconds: { used: number | null; limit: number | null };
    shared_remaining: {
      tokens: number | null;
      cost_usd: string | null;
      cost_unknown_events: number;
    };
  };
}

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
  resource?: TaskResourceView;
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
