import { jsonFetch } from "../fetch-client";

export type ActiveToolCall = {
  id: string; execution_id: string; session_id: string; tool_call_id?: string | null;
  name: string; description: string; command: string; prompt?: string; started_at: number;
  status: "running" | "unknown";
};

/** An `agent` call that has returned, paired with the sub-agent it started.
 *  `job_id` is null when it started none; `owner_job_id` names the
 *  sub-agent that made the call, null on the main conversation. */
export type AgentCall = {
  id: string; caller: string; owner_job_id: string | null; job_id: string | null;
  description: string; prompt: string; at: number; outcome?: string | null; failed: boolean; error: string;
};

export type ManagedProcess = {
  id: string;
  session_id: string | null;
  execution_id: string | null;
  tool_call_id?: string | null;
  command: string;
  display?: { name: string; kind: "executable" | "script" | "module" | "snippet" | "unknown" };
  cwd: string | null;
  status: string;
  started_at: number;
  ended_at: number | null;
  pid: number | null;
  exit_code: number | null;
  backend_id: string;
  truncated: boolean;
  can_stop?: boolean;
  /** Started with a pseudo-terminal, so its output carries terminal control codes. */
  tty?: boolean;
  /** A bash call returned while it was still running: a background terminal. */
  background?: boolean;
  /** What the command does, in the model's words ("Evaluate 100 samples"). */
  title?: string | null;
  /** How far it is, read from its output (openprogram/host/processes/progress.py). */
  progress?: ProcessProgress | null;
  /** The last line of output with text, without escape codes. */
  last_line?: string | null;
};

/** ``current`` of ``total``; ``total`` null means the amount is unknown. */
export type ProcessProgress = {
  source: "marker" | "osc" | "bar" | "counter";
  state: "normal" | "error" | "paused" | "indeterminate";
  current: number | null;
  total: number | null;
  /** What is counted ("samples", "epochs") or, for a bar, its description. */
  label?: string | null;
  unit?: string | null;
  /** Items per second and seconds left, when known. */
  rate?: number | null;
  eta?: number | null;
  updated_at: number;
};
export const processIsActive = (process: ManagedProcess) =>
  ["starting", "running", "stopping", "unknown"].includes(process.status);
export const getSessionProcesses = (sessionId: string, signal?: AbortSignal) =>
  jsonFetch<{ items: ManagedProcess[]; calls?: ActiveToolCall[]; spawns?: AgentCall[]; now: number }>(`/api/session/${encodeURIComponent(sessionId)}/processes`, { signal, cache: "no-store" });
export const getProcess = (id: string, signal?: AbortSignal, sessionId?: string | null) =>
  jsonFetch<{ process: ManagedProcess; output: string }>(`/api/process/${encodeURIComponent(id)}${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`, { signal, cache: "no-store" });
export const stopProcess = (id: string, sessionId?: string | null) =>
  jsonFetch<{ process: ManagedProcess }>(`/api/process/${encodeURIComponent(id)}/stop${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`, { method: "POST", body: JSON.stringify({}), headers: { "Content-Type": "application/json" } });
