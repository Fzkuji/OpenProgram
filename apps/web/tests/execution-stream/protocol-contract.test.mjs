/**
 * Self-contained execution_stream.v1 contract tests (pure JS).
 * Mirrors apps/web/lib/execution-stream/reducer.ts apply() rules so
 * this Node 20 host can verify the protocol without --experimental-strip-types.
 */
import assert from "node:assert/strict";
import test from "node:test";

const TERMINAL = new Set(["completed", "cancelled", "failed"]);

function empty(partial) {
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
    preview_reasoning: "",
    current_attempt_id: partial.current_attempt_id ?? null,
    syncing: false,
    terminal: !!partial.terminal,
  };
}

function recompute(state) {
  const aid = state.current_attempt_id
    || state.attempts[state.attempts.length - 1]?.attempt_id;
  const attempt = state.attempts.find((a) => a.attempt_id === aid);
  if (!attempt) return { ...state, preview_text: "" };
  let text = "";
  for (const b of attempt.blocks) {
    if (b.kind === "text" || b.kind === "refusal") text += b.content;
  }
  return { ...state, preview_text: text };
}

function apply(state, event) {
  let cur = state || empty({
    session_id: event.session_id,
    execution_id: event.execution_id,
    node_id: event.node_id,
  });
  const op = event.op;
  if (op === "snapshot" || op === "node_finished") {
    if (event.generation < cur.generation) return { ok: false, state: cur, reason: "stale_generation" };
    if (event.generation > cur.generation) {
      if (cur.terminal) return { ok: false, state: cur, reason: "terminal_reopen_rejected" };
      const snap = event.snapshot || {};
      return {
        ok: true,
        state: {
          ...cur,
          generation: event.generation,
          revision: snap.revision ?? event.revision ?? 0,
          phase: snap.phase || event.phase || cur.phase,
          preview_text: snap.preview_text || "",
          attempts: snap.attempts || [],
          current_attempt_id: snap.current_attempt_id ?? null,
          terminal: op === "node_finished" || TERMINAL.has(String(snap.phase || "")),
          syncing: false,
        },
      };
    }
    const incomingRev = (event.snapshot?.revision) ?? event.revision ?? 0;
    if (incomingRev < cur.revision) return { ok: false, state: cur, reason: "stale_revision" };
    if (cur.terminal && !TERMINAL.has(String(event.snapshot?.phase || event.phase || ""))) {
      return { ok: false, state: cur, reason: "terminal_to_running" };
    }
    const snap = event.snapshot || {};
    const next = {
      ...cur,
      revision: incomingRev,
      phase: snap.phase || event.phase || cur.phase,
      preview_text: snap.preview_text ?? cur.preview_text,
      attempts: snap.attempts || cur.attempts,
      current_attempt_id: snap.current_attempt_id ?? cur.current_attempt_id,
      terminal: op === "node_finished" || TERMINAL.has(String(snap.phase || event.phase || "")),
      syncing: false,
    };
    return { ok: true, state: next };
  }
  if (event.generation !== cur.generation) {
    return { ok: false, state: { ...cur, syncing: true }, reason: "generation_mismatch", requestSnapshot: true };
  }
  if ((event.revision ?? -1) <= cur.revision) return { ok: true, state: cur };
  if ((event.base_revision ?? -1) !== cur.revision) {
    return { ok: false, state: { ...cur, syncing: true }, reason: "gap", requestSnapshot: true };
  }
  if (cur.terminal) return { ok: false, state: cur, reason: "late_update_rejected" };

  let next = { ...cur, revision: event.revision, syncing: false };
  if (op === "attempt_started") {
    next = {
      ...next,
      attempts: [
        ...next.attempts.map((a) => (a.status === "running" ? { ...a, status: "superseded" } : a)),
        {
          attempt_id: event.attempt_id,
          attempt_index: event.attempt_index ?? next.attempts.length,
          status: "running",
          blocks: [],
        },
      ],
      current_attempt_id: event.attempt_id,
      preview_text: "",
    };
  } else if (op === "block_started") {
    const attempts = next.attempts.slice();
    let idx = attempts.findIndex((a) => a.attempt_id === event.attempt_id);
    if (idx < 0) {
      attempts.push({ attempt_id: event.attempt_id, attempt_index: attempts.length, status: "running", blocks: [] });
      idx = attempts.length - 1;
    }
    if (!attempts[idx].blocks.some((b) => b.block_id === event.block_id)) {
      attempts[idx] = {
        ...attempts[idx],
        blocks: [...attempts[idx].blocks, {
          block_id: event.block_id, kind: event.kind || "text", content: "", status: "running",
        }],
      };
    }
    next = { ...next, attempts };
  } else if (op === "block_delta") {
    const attempts = next.attempts.slice();
    const idx = attempts.findIndex((a) => a.attempt_id === event.attempt_id);
    if (idx < 0) return { ok: false, state: cur, reason: "unknown_attempt", requestSnapshot: true };
    const blk = attempts[idx].blocks.find((b) => b.block_id === event.block_id);
    if (!blk || blk.status !== "running") return { ok: false, state: cur, reason: "delta_after_finished" };
    attempts[idx] = {
      ...attempts[idx],
      blocks: attempts[idx].blocks.map((b) =>
        b.block_id === event.block_id ? { ...b, content: b.content + (event.delta || "") } : b),
    };
    next = recompute({ ...next, attempts });
  } else if (op === "node_finished") {
    next = { ...next, terminal: true, phase: event.phase || "completed" };
  } else {
    return { ok: false, state: { ...cur, syncing: true }, reason: `unknown_op:${op}`, requestSnapshot: true };
  }
  return { ok: true, state: next };
}

function ev(over) {
  return {
    type: "execution_stream", schema_version: 1,
    session_id: "s", execution_id: "e", node_id: "n", generation: 1, ...over,
  };
}

test("gap does not append AC; snapshot restores ABC then D", () => {
  let r = apply(null, ev({
    op: "snapshot", revision: 1,
    snapshot: {
      revision: 1, phase: "running", preview_text: "A",
      current_attempt_id: "a1",
      attempts: [{
        attempt_id: "a1", status: "running",
        blocks: [{ block_id: "b1", kind: "text", content: "A", status: "running" }],
      }],
    },
  }));
  assert.equal(r.state.preview_text, "A");
  r = apply(r.state, ev({
    op: "block_delta", base_revision: 2, revision: 3,
    attempt_id: "a1", block_id: "b1", delta: "C",
  }));
  assert.equal(r.reason, "gap");
  assert.equal(r.state.preview_text, "A");
  r = apply(r.state, ev({
    op: "snapshot", revision: 3,
    snapshot: {
      revision: 3, phase: "running", preview_text: "ABC",
      current_attempt_id: "a1",
      attempts: [{
        attempt_id: "a1", status: "running",
        blocks: [{ block_id: "b1", kind: "text", content: "ABC", status: "running" }],
      }],
    },
  }));
  r = apply(r.state, ev({
    op: "block_delta", base_revision: 3, revision: 4,
    attempt_id: "a1", block_id: "b1", delta: "D",
  }));
  assert.equal(r.state.preview_text, "ABCD");
});

test("retry clears current preview", () => {
  let r = apply(null, ev({
    op: "snapshot", revision: 1,
    snapshot: {
      revision: 1, preview_text: "old", current_attempt_id: "a1",
      attempts: [{
        attempt_id: "a1", status: "running",
        blocks: [{ block_id: "b1", kind: "text", content: "old", status: "running" }],
      }],
    },
  }));
  r = apply(r.state, ev({
    op: "attempt_started", base_revision: 1, revision: 2, attempt_id: "a2",
  }));
  assert.equal(r.state.preview_text, "");
  r = apply(r.state, ev({
    op: "block_started", base_revision: 2, revision: 3,
    attempt_id: "a2", block_id: "b2", kind: "text",
  }));
  r = apply(r.state, ev({
    op: "block_delta", base_revision: 3, revision: 4,
    attempt_id: "a2", block_id: "b2", delta: "new",
  }));
  assert.equal(r.state.preview_text, "new");
});

test("terminal rejects late delta", () => {
  let r = apply(null, ev({
    op: "node_finished", revision: 2, phase: "completed",
    snapshot: { revision: 2, phase: "completed", preview_text: "done", attempts: [] },
  }));
  assert.equal(r.state.terminal, true);
  r = apply(r.state, ev({
    op: "block_delta", base_revision: 2, revision: 3,
    attempt_id: "a", block_id: "b", delta: "x",
  }));
  assert.equal(r.reason, "late_update_rejected");
});

test("G=2/R=0 replaces G=1/R=500; old gen rejected", () => {
  let r = apply(empty({
    session_id: "s", execution_id: "e", node_id: "n", generation: 1, revision: 500,
  }), ev({
    generation: 2, op: "snapshot", revision: 0,
    snapshot: { revision: 0, phase: "running", preview_text: "recovered", attempts: [] },
  }));
  assert.equal(r.state.generation, 2);
  assert.equal(r.state.revision, 0);
  const late = apply(r.state, ev({
    generation: 1, op: "block_delta", base_revision: 500, revision: 501,
    attempt_id: "a", block_id: "b", delta: "x",
  }));
  assert.equal(late.requestSnapshot, true);
});
