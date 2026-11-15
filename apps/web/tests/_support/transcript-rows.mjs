// Rows for a test, written the way the product writes them: each server row is
// one turn_upsert through apply-transcript-update.ts, each browser-only row one
// typed local change, and every read goes through selectRow(viewKey, id). The
// store has no other writer, in tests as in the product.
import { applyTranscriptChanges } from "../../lib/chat/transcript/apply-transcript-update.ts";

/** Row helpers bound to one session store (the module a test imported). */
export function transcriptRows(useSessionStore, selectRow) {
  const store = () => useSessionStore.getState();
  const view = (key) => {
    const state = store();
    return {
      order: state.messageOrder[key] ?? [],
      rows: state.rowsByView[key] ?? {},
      overlays: state.overlaysByView[key],
      heldFrames: state.heldFramesByView?.[key],
    };
  };
  const apply = (key, changes) => {
    const next = applyTranscriptChanges(view(key), changes, { holdsTurn: () => false, atLatest: true });
    store().writeView(key, next);
  };
  const turnOf = (row) => ({ id: row.turnId ?? row.id, rows: [row] });

  /** A server row at the end of the view, or the same row replaced in place. */
  function putRow(key, row) {
    const held = store().rowsByView[key]?.[row.id];
    if (held) apply(key, [{ kind: "row_upsert", turnId: row.turnId ?? row.id, afterRowId: null, row: { ...row } }]);
    else apply(key, [{ kind: "turn_upsert", turn: turnOf({ ...row }), afterTurnId: null }]);
    return readRow(key, row.id);
  }
  /** Server rows in order, after whatever the view holds. */
  function putRows(key, rows) {
    for (const row of rows) putRow(key, row);
  }
  /** The view holds exactly these server rows. */
  function replaceRows(key, rows) {
    store().applyRead(key, rows.map((row) => ({ ...row })));
  }
  /** Change fields of a server row; null or undefined takes a field off. */
  function patchRow(key, rowId, fields) {
    const held = store().rowsByView[key]?.[rowId];
    if (!held) return;
    const next = { ...held };
    for (const [name, value] of Object.entries(fields)) {
      if (value === undefined || value === null) delete next[name];
      else next[name] = value;
    }
    putRow(key, next);
  }
  /** A browser-only row: a send, placeholder or notice. */
  function localRow(key, row, afterRowId) {
    store().applyLocalChange(key, { kind: "local_append", row: { rowKind: row.role === "user" ? "user" : "assistant", ...row }, afterRowId });
  }
  function removeRow(key, rowId) {
    store().applyLocalChange(key, { kind: "local_remove", rowId });
  }
  function readRow(key, rowId) {
    return selectRow(key, rowId)(store());
  }
  function readRows(key) {
    return (store().messageOrder[key] ?? []).map((id) => readRow(key, id)).filter(Boolean);
  }
  return { putRow, putRows, replaceRows, patchRow, localRow, removeRow, readRow, readRows };
}
