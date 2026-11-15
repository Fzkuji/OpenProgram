/**
 * Versioned apply reducer for execution_stream.v1 (design §5).
 *
 * Gaps → sync_required semantics (requestSnapshot); never blind append.
 * Generation dominates revision. Terminal states reject late running updates.
 */
import type {
  ApplyResult,
  ExecutionStreamEvent,
  NodeStreamState,
  SnapshotPayload,
  StreamAttempt,
  StreamBlock,
} from "./types";
import { EXECUTION_STREAM_SCHEMA_VERSION } from "./types";

const TERMINAL = new Set([
  "completed",
  "cancelled",
  "failed",
]);

export function emptyNodeState(
  partial: Pick<
    NodeStreamState,
    "session_id" | "execution_id" | "node_id"
  > &
    Partial<NodeStreamState>,
): NodeStreamState {
  return {
    session_id: partial.session_id,
    execution_id: partial.execution_id,
    node_id: partial.node_id,
    generation: partial.generation ?? 0,
    revision: partial.revision ?? 0,
    display_msg_id: partial.display_msg_id ?? "",
    phase: partial.phase ?? "created",
    attempts: partial.attempts ?? [],
    preview_text: partial.preview_text ?? "",
    preview_reasoning: partial.preview_reasoning ?? "",
    current_attempt_id: partial.current_attempt_id ?? null,
    selected_attempt_id: partial.selected_attempt_id ?? null,
    syncing: partial.syncing ?? false,
    terminal: partial.terminal ?? false,
    truncated: partial.truncated ?? false,
    durability: partial.durability,
  };
}

function cmpVersion(
  aGen: number,
  aRev: number,
  bGen: number,
  bRev: number,
): number {
  if (aGen !== bGen) return aGen < bGen ? -1 : 1;
  if (aRev !== bRev) return aRev < bRev ? -1 : 1;
  return 0;
}

function recomputePreviews(state: NodeStreamState): NodeStreamState {
  const aid =
    state.current_attempt_id ||
    state.attempts[state.attempts.length - 1]?.attempt_id;
  const attempt = state.attempts.find((a) => a.attempt_id === aid);
  if (!attempt) {
    return { ...state, preview_text: "", preview_reasoning: "" };
  }
  let text = "";
  let reasoning = "";
  for (const b of attempt.blocks) {
    if (b.omitted_by_policy || b.visibility === "opaque") continue;
    if (b.kind === "text" || b.kind === "refusal") text += b.content;
    if (b.kind === "reasoning_summary") reasoning += b.content;
  }
  return { ...state, preview_text: text, preview_reasoning: reasoning };
}

function replaceFromSnapshot(
  state: NodeStreamState,
  event: ExecutionStreamEvent,
  snap: SnapshotPayload,
): NodeStreamState {
  const attempts: StreamAttempt[] = (snap.attempts || []).map((a) => ({
    ...a,
    blocks: (a.blocks || []).map((b) => ({ ...b, content: b.content || "" })),
  }));
  const next: NodeStreamState = {
    ...state,
    session_id: event.session_id,
    execution_id: event.execution_id,
    node_id: event.node_id,
    generation: event.generation,
    revision: snap.revision ?? event.revision ?? 0,
    display_msg_id: event.display_msg_id || state.display_msg_id,
    phase: snap.phase || event.phase || state.phase,
    current_attempt_id: snap.current_attempt_id ?? null,
    selected_attempt_id: snap.selected_attempt_id ?? null,
    attempts,
    preview_text: snap.preview_text || "",
    preview_reasoning: snap.preview_reasoning || "",
    syncing: false,
    terminal: TERMINAL.has(String(snap.phase || event.phase || "")),
    durability: snap.durability,
    truncated: attempts.some((a) => a.blocks.some((b) => b.truncated)),
  };
  return recomputePreviews(next);
}

function upsertAttempt(
  attempts: StreamAttempt[],
  patch: Partial<StreamAttempt> & { attempt_id: string },
): StreamAttempt[] {
  const idx = attempts.findIndex((a) => a.attempt_id === patch.attempt_id);
  if (idx < 0) {
    return [
      ...attempts,
      {
        attempt_id: patch.attempt_id,
        attempt_index: patch.attempt_index ?? attempts.length,
        reason: patch.reason,
        status: patch.status || "running",
        validation: patch.validation || "pending",
        provider: patch.provider,
        model: patch.model,
        parent_attempt_id: patch.parent_attempt_id,
        message_id: patch.message_id,
        blocks: patch.blocks || [],
      },
    ];
  }
  const next = attempts.slice();
  next[idx] = { ...next[idx], ...patch, blocks: patch.blocks || next[idx].blocks };
  return next;
}

function upsertBlock(
  attempt: StreamAttempt,
  patch: Partial<StreamBlock> & { block_id: string },
): StreamAttempt {
  const blocks = attempt.blocks.slice();
  const idx = blocks.findIndex((b) => b.block_id === patch.block_id);
  // Drop undefined so finish events do not wipe tool_ref metadata.
  const clean = Object.fromEntries(
    Object.entries(patch).filter(([, v]) => v !== undefined),
  ) as Partial<StreamBlock> & { block_id: string };
  if (idx < 0) {
    blocks.push({
      block_id: clean.block_id,
      message_id: clean.message_id || attempt.message_id || "",
      block_index: clean.block_index ?? blocks.length,
      kind: clean.kind || "unsupported",
      visibility: clean.visibility || "visible",
      retention: clean.retention || "persist",
      content: clean.content || "",
      status: clean.status || "running",
      finish_reason: clean.finish_reason,
      omitted_by_policy: clean.omitted_by_policy,
      truncated: clean.truncated,
      tool_call_id: clean.tool_call_id,
      occurrence_id: clean.occurrence_id,
      ref_node_id: clean.ref_node_id,
      tool_name: clean.tool_name,
      group_id: clean.group_id,
    });
  } else {
    blocks[idx] = { ...blocks[idx], ...clean };
  }
  return { ...attempt, blocks };
}

export function applyExecutionStreamEvent(
  state: NodeStreamState | null,
  event: ExecutionStreamEvent,
): ApplyResult {
  if (!event || event.type !== "execution_stream") {
    const s = state || emptyNodeState({
      session_id: "",
      execution_id: "",
      node_id: "",
    });
    return { ok: false, state: s, reason: "invalid_event" };
  }
  if (event.schema_version !== EXECUTION_STREAM_SCHEMA_VERSION) {
    const s = state || emptyNodeState({
      session_id: event.session_id,
      execution_id: event.execution_id,
      node_id: event.node_id,
      generation: event.generation,
    });
    return { ok: false, state: s, reason: "schema_mismatch", requestSnapshot: true };
  }

  let cur =
    state ||
    emptyNodeState({
      session_id: event.session_id,
      execution_id: event.execution_id,
      node_id: event.node_id,
      generation: 0,
      revision: 0,
    });

  // Identity mismatch → ignore (wrong subscription).
  if (
    state &&
    (state.session_id !== event.session_id ||
      state.execution_id !== event.execution_id ||
      state.node_id !== event.node_id)
  ) {
    return { ok: false, state: cur, reason: "identity_mismatch" };
  }

  if (event.display_msg_id) {
    cur = { ...cur, display_msg_id: event.display_msg_id };
  }

  const op = event.op;

  if (op === "snapshot" || op === "node_finished") {
    const snap =
      event.snapshot ||
      ({
        revision: event.revision ?? event.terminal_revision ?? 0,
        phase: event.phase || event.status,
        checkpoint_revision: event.checkpoint_revision,
      } as SnapshotPayload);

    if (event.generation < cur.generation) {
      return { ok: false, state: cur, reason: "stale_generation" };
    }
    if (event.generation > cur.generation) {
      // Authorized owner takeover: replace even when revision is smaller.
      if (cur.terminal && TERMINAL.has(String(cur.phase))) {
        // Durable terminal must not reopen under a new generation with
        // the same node identity — reject.
        return { ok: false, state: cur, reason: "terminal_reopen_rejected" };
      }
      const next = replaceFromSnapshot(cur, event, snap);
      if (op === "node_finished") {
        next.terminal = true;
        next.phase = event.phase || event.status || snap.phase || "completed";
      }
      return { ok: true, state: next };
    }
    // Same generation.
    const incomingRev = snap.revision ?? event.revision ?? 0;
    if (incomingRev < cur.revision) {
      return { ok: false, state: cur, reason: "stale_revision" };
    }
    if (cur.terminal && !TERMINAL.has(String(snap.phase || event.phase || ""))) {
      return { ok: false, state: cur, reason: "terminal_to_running" };
    }
    const next = replaceFromSnapshot(cur, event, snap);
    if (op === "node_finished") {
      next.terminal = true;
      next.phase = event.phase || event.status || next.phase;
    }
    return { ok: true, state: next };
  }

  if (op === "sync_required") {
    if (cur.terminal || event.generation < cur.generation) {
      return { ok: true, state: cur };
    }
    if (event.reason === "owner_unavailable") {
      // This is the answer to a recovery request, not another request.
      // Keep the transcript while waiting for a future authoritative event.
      return {
        ok: true,
        state: { ...cur, syncing: false, last_error: "owner_unavailable" },
      };
    }
    return {
      ok: true,
      state: { ...cur, syncing: true },
      requestSnapshot: true,
    };
  }

  // A late delta cannot restart recovery for a completed node.
  if (cur.terminal) {
    return { ok: false, state: cur, reason: "late_update_rejected" };
  }
  // Incremental ops.
  if (event.generation !== cur.generation) {
    return {
      ok: false,
      state: { ...cur, syncing: true },
      reason: "generation_mismatch",
      requestSnapshot: true,
    };
  }
  const rev = event.revision ?? -1;
  const base = event.base_revision ?? -1;
  if (rev <= cur.revision) {
    return { ok: true, state: cur }; // duplicate / old
  }
  if (base !== cur.revision) {
    return {
      ok: false,
      state: { ...cur, syncing: true },
      reason: "gap",
      requestSnapshot: true,
    };
  }
  let next: NodeStreamState = { ...cur, revision: rev, syncing: false };

  switch (op) {
    case "attempt_started": {
      // Mark previous running as superseded locally if still marked running.
      let attempts = next.attempts.map((a) =>
        a.status === "running" ? { ...a, status: "superseded" } : a,
      );
      attempts = upsertAttempt(attempts, {
        attempt_id: event.attempt_id || `attempt_${rev}`,
        attempt_index: event.attempt_index,
        reason: event.reason,
        status: "running",
        provider: event.provider,
        model: event.model,
        parent_attempt_id: event.parent_attempt_id,
        message_id: event.message_id,
        blocks: [],
      });
      next = {
        ...next,
        attempts,
        current_attempt_id: event.attempt_id,
        phase: event.phase || "running",
        preview_text: "",
        preview_reasoning: "",
      };
      break;
    }
    case "block_started": {
      const aid = event.attempt_id || next.current_attempt_id;
      if (!aid) {
        return { ok: false, state: cur, reason: "missing_attempt", requestSnapshot: true };
      }
      const attempts = next.attempts.slice();
      let idx = attempts.findIndex((a) => a.attempt_id === aid);
      if (idx < 0) {
        attempts.push({
          attempt_id: aid,
          attempt_index: attempts.length,
          status: "running",
          blocks: [],
          message_id: event.message_id,
        });
        idx = attempts.length - 1;
      }
      // Idempotent: existing block_id is fine.
      if (!attempts[idx].blocks.some((b) => b.block_id === event.block_id)) {
        attempts[idx] = upsertBlock(attempts[idx], {
          block_id: event.block_id || `block_${rev}`,
          message_id: event.message_id || attempts[idx].message_id || "",
          block_index: event.block_index ?? attempts[idx].blocks.length,
          kind: event.kind || "unsupported",
          visibility: event.visibility,
          retention: event.retention,
          content: "",
          status: event.status || "running",
          tool_call_id: event.tool_call_id,
          occurrence_id: event.occurrence_id,
          ref_node_id: event.ref_node_id,
          tool_name: event.tool_name,
          group_id: event.group_id,
        });
      }
      next = { ...next, attempts };
      break;
    }
    case "block_delta": {
      const aid = event.attempt_id || next.current_attempt_id;
      const bid = event.block_id;
      if (!aid || !bid) {
        return { ok: false, state: cur, reason: "missing_block", requestSnapshot: true };
      }
      const attempts = next.attempts.slice();
      const idx = attempts.findIndex((a) => a.attempt_id === aid);
      if (idx < 0) {
        return { ok: false, state: { ...cur, syncing: true }, reason: "unknown_attempt", requestSnapshot: true };
      }
      const blk = attempts[idx].blocks.find((b) => b.block_id === bid);
      if (!blk) {
        return { ok: false, state: { ...cur, syncing: true }, reason: "unknown_block", requestSnapshot: true };
      }
      if (blk.status !== "running") {
        return { ok: false, state: cur, reason: "delta_after_finished" };
      }
      if (blk.visibility === "opaque") {
        next = { ...next, attempts };
        break;
      }
      attempts[idx] = upsertBlock(attempts[idx], {
        block_id: bid,
        content: blk.content + (event.delta || ""),
      });
      next = recomputePreviews({ ...next, attempts, phase: "running" });
      break;
    }
    case "block_finished": {
      const aid = event.attempt_id || next.current_attempt_id;
      const bid = event.block_id;
      if (!aid || !bid) break;
      const attempts = next.attempts.slice();
      const idx = attempts.findIndex((a) => a.attempt_id === aid);
      if (idx < 0) break;
      attempts[idx] = upsertBlock(attempts[idx], {
        block_id: bid,
        status: "finished",
        finish_reason: event.finish_reason,
        tool_call_id: event.tool_call_id,
        occurrence_id: event.occurrence_id,
        ref_node_id: event.ref_node_id,
        tool_name: event.tool_name,
        group_id: event.group_id,
      });
      next = { ...next, attempts };
      break;
    }
    case "attempt_finished": {
      const aid = event.attempt_id || next.current_attempt_id;
      if (!aid) break;
      const attempts = upsertAttempt(next.attempts, {
        attempt_id: aid,
        status: event.status || "completed",
        validation: event.validation,
        usage: event.usage,
      });
      // Close any still-running blocks.
      const aidx = attempts.findIndex((a) => a.attempt_id === aid);
      if (aidx >= 0) {
        attempts[aidx] = {
          ...attempts[aidx],
          blocks: attempts[aidx].blocks.map((b) =>
            b.status === "running"
              ? { ...b, status: "finished", finish_reason: "attempt_end" }
              : b,
          ),
        };
      }
      next = { ...next, attempts };
      break;
    }
    default:
      // Unknown state-changing op → sync, do not advance blindly.
      return {
        ok: false,
        state: { ...cur, syncing: true },
        reason: `unknown_op:${op}`,
        requestSnapshot: true,
      };
  }

  return { ok: true, state: next };
}

/** Compare helper exported for tests. */
export function compareStreamVersion(
  aGen: number,
  aRev: number,
  bGen: number,
  bRev: number,
): number {
  return cmpVersion(aGen, aRev, bGen, bRev);
}

export function selectReplyPreview(state: NodeStreamState | null | undefined): string {
  if (!state) return "";
  return state.preview_text || "";
}

export function selectReasoningPreview(
  state: NodeStreamState | null | undefined,
): string {
  if (!state) return "";
  return state.preview_reasoning || "";
}
