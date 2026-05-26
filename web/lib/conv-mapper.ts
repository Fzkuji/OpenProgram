/**
 * Map a legacy conversation payload (`conversations[id].messages`, the
 * shape `conversations.js` / `chat-ws.js` build) into the normalized
 * `ChatMsg[]` the React message store consumes.
 *
 * Phase 3 plumbing: `renderSessionMessages` calls this through the
 * `window.__feedStoreFromConv` bridge so the React store mirrors the
 * loaded conversation. The legacy DOM renderer still runs in parallel
 * until the cutover flip — feeding the store is additive.
 */
import type { ChatMsg, ChatToolCall } from "./session-store";

/** Same allowlist as the one in ``chat-stream.ts``. Hides any persisted
 *  agentic tool block on history reload so we don't show both the
 *  folded chat-tool card AND the standalone RuntimeBlock for one call. */
const AGENTIC_TOOL_NAMES: ReadonlySet<string> = new Set([
  "gui_agent",
  "research_agent",
  "wiki_agent",
]);

interface LegacyBlock {
  type?: string;
  text?: string;
  tool?: string;
  input?: string;
  result?: unknown;
  is_error?: boolean;
  tool_call_id?: string;
}

interface LegacyAttempt {
  content?: string;
  timestamp?: number;
  tree?: unknown;
  usage?: unknown;
}

interface LegacyMsg {
  role?: string;
  content?: string;
  type?: string;
  function?: string | null;
  display?: string;
  blocks?: LegacyBlock[];
  id?: string;
  timestamp?: number;
  created_at?: number;
  context_tree?: unknown;
  usage?: unknown;
  attempts?: LegacyAttempt[];
  current_attempt?: number;
  tool_calls?: LegacyBlock[];
  sibling_index?: number;
  sibling_total?: number;
  prev_sibling_id?: string;
  next_sibling_id?: string;
  /** Top-level field hoisted by ``_msg_adapter`` for assistant rows
   *  whose ``extra`` carried an ``attach`` blob. */
  attach?: AttachMeta;
  extra?: string | { attach?: AttachMeta; [k: string]: unknown };
  /** Which agent produced this turn — stamped by the dispatcher on
   *  both user and assistant rows. Same-session multi-agent uses
   *  this to colour / label each row by author. */
  agent_id?: string;
  /** streaming-resume: lifecycle status persisted on the node.
   *  ``running`` means a producer is still writing this msg — render
   *  the runtime block in its in-progress state and (if the worker
   *  is reachable) subscribe for live updates. Missing == ``done``
   *  for backward compat with pre-streaming-resume sessions. */
  status?: "pending" | "running" | "streaming" | "done"
    | "completed" | "error" | "cancelled" | "interrupted";
  /** streaming-resume: when the placeholder reply was first written.
   *  Used by sweep to detect orphaned ``running`` rows. */
  started_at?: number;
  last_update_at?: number;
  parent_id?: string;
}

export interface AttachMeta {
  session_id?: string;
  head_id?: string;
  commit_id?: string;
  label?: string;
  prompt?: string;
  /** True when the attach pointer was written by the user via the
   *  Branches → Attach to flow (rather than a /task spawn). The card
   *  uses this to surface the staged-reference UI. */
  manual?: boolean;
  /** Pinned source ContextCommit id — the snapshot the generator
   *  will expand into the next turn. Absent on legacy attach rows
   *  written before the expansion refactor. */
  source_commit_id?: string;
  /** Count of items in the pinned source commit (frontend renders
   *  this in the embed preview). Computed server-side. */
  embed_count?: number;
  /** Token sum across the source commit's items. */
  embed_tokens?: number;
  /** Async-task lifecycle status, written by the runner as the
   *  spawned sub-agent moves through pending → running →
   *  completed / errored / cancelled. The card uses it to show a
   *  live status pill so the user can tell whether the embedded
   *  content is still being filled in. */
  status?: "pending" | "queued" | "running" | "completed"
    | "errored" | "cancelled";
  /** Cross-reference to the Task entity that produced this attach
   *  (when one exists — manual attaches don't have a task). */
  task_id?: string;
}

function _readAttach(m: LegacyMsg): AttachMeta | undefined {
  if (m.attach && typeof m.attach === "object") return m.attach;
  const e = m.extra;
  if (e && typeof e === "object" && !Array.isArray(e)) {
    const a = (e as { attach?: AttachMeta }).attach;
    if (a && typeof a === "object") return a;
  }
  if (typeof e === "string" && e) {
    try {
      const parsed = JSON.parse(e);
      if (parsed && typeof parsed === "object" && parsed.attach) {
        return parsed.attach as AttachMeta;
      }
    } catch {
      /* ignore */
    }
  }
  return undefined;
}

/** Sibling-version fields shared by user + assistant turns. */
function siblingFields(m: LegacyMsg) {
  return {
    siblingIndex: m.sibling_index,
    siblingTotal: m.sibling_total,
    prevSiblingId: m.prev_sibling_id,
    nextSiblingId: m.next_sibling_id,
  };
}

export function convToChatMsgs(messages: LegacyMsg[]): ChatMsg[] {
  const out: ChatMsg[] = [];
  messages.forEach((m, i) => {
    // streaming-resume: a ``type: "status"`` row with display=runtime
    // is the runner's persisted reply (placeholder when running,
    // finalized when done). Either way it must render — the status
    // field drives the visual state (running spinner vs static
    // tree). Legacy non-runtime ``status`` rows (transient "Running
    // foo..." pings) are still hidden.
    const _isRuntimePlaceholder =
      m.type === "status" && m.display === "runtime";
    const _isRunningPlaceholder =
      _isRuntimePlaceholder && m.status === "running";
    if (m.type === "status" && !_isRuntimePlaceholder) return;
    const id = m.id || `hist_${i}`;
    const ts = m.timestamp || m.created_at;

    if (m.role === "user") {
      const sf = (m as { spawned_from?: { caller_id?: string; label?: string | null } }).spawned_from;
      out.push({
        id,
        role: "user",
        content: m.content || "",
        display: m.display === "runtime" ? "runtime" : undefined,
        status: "done",
        timestamp: ts,
        agentId: m.agent_id || undefined,
        source: typeof m.source === "string" ? m.source : undefined,
        parentId: typeof m.parent_id === "string" ? m.parent_id : undefined,
        spawnedFrom: sf && sf.caller_id
          ? { callerId: sf.caller_id, label: sf.label || undefined }
          : undefined,
        ...siblingFields(m),
      });
      return;
    }

    if (m.role === "assistant") {
      let thinking: string | undefined;
      const tools: ChatToolCall[] = [];
      // Backfill: pre-`blocks` messages only carry slim `tool_calls`.
      const rawBlocks =
        m.blocks && m.blocks.length
          ? m.blocks
          : (m.tool_calls || []).map((tc) => ({ type: "tool", ...tc }));
      rawBlocks.forEach((b, bi) => {
        if (b.type === "thinking" && b.text) {
          thinking = (thinking ?? "") + b.text;
        } else if (b.type === "tool") {
          if (b.tool && AGENTIC_TOOL_NAMES.has(b.tool)) return;
          tools.push({
            id: b.tool_call_id || `${id}_t${bi}`,
            tool: b.tool || "?",
            input: b.input || "",
            result:
              b.result === undefined || b.result === null
                ? undefined
                : String(b.result),
            isError: !!b.is_error,
            status: b.is_error ? "error" : "done",
          });
        }
      });
      out.push({
        id,
        role: "assistant",
        content: m.content || "",
        thinking,
        tools: tools.length ? tools : undefined,
        function: m.function || undefined,
        display: m.display === "runtime" ? "runtime" : undefined,
        status: (() => {
          // streaming-resume: respect the persisted status when it's
          // a recognised lifecycle value. Fall back to the legacy
          // type-derived rule so older rows (without a status meta
          // field) still render correctly.
          const _s = m.status;
          if (_s === "running" || _isRunningPlaceholder) return "running";
          if (_s === "cancelled") return "cancelled";
          if (_s === "interrupted") return "interrupted";
          if (_s === "error") return "error";
          if (_s === "streaming") return "streaming";
          return m.type === "error" ? "error" : "done";
        })(),
        rawType: m.type,
        timestamp: ts,
        contextTree: (m.context_tree as never) || undefined,
        usage: m.usage,
        attempts: m.attempts as never[] | undefined,
        current_attempt: m.current_attempt,
        attach: _readAttach(m),
        agentId: m.agent_id || undefined,
        ...siblingFields(m),
      });
      return;
    }

    out.push({ id, role: "system", content: m.content || "", status: "done" });
  });
  return out;
}
