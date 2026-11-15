/**
 * Deleting selected DAG nodes, and undoing it (dag/rendering.md §13).
 *
 * A delete is immediate: the selection goes to the server
 * (``delete_nodes``), the store splices the successors onto the nearest
 * surviving predecessor and keeps the removed files in a trash journal,
 * and every client reloads the session. ⌘Z — or the "Undo" on the
 * canvas action bar — sends the journal's id back
 * (``restore_deleted_nodes``). The undo stack is per session and lives
 * as long as the page.
 *
 * What a selected glyph stands for is not always one row: a followup
 * reply merges into the node before it, and a spawn square stands for
 * the dispatch call that opened it. The selection is widened to the
 * rows the glyph shows before it is sent, so deleting a glyph deletes
 * what the user saw. Structure nodes (the root, a compaction summary,
 * attach/merge machinery) are left out; the store refuses them anyway.
 */

import { translateText } from "@/lib/i18n";
import { getSocket, runtimeState } from "../../../state";
import type { GNode } from "../../types";
import { _lastGraph, _lastThreadModel } from "../../store/globals";
import { isSpawnRoot } from "../../passes/thread-nodes";
import { clearSelection, emit, selectedIds } from "./selection";

export const DAG_NODES_EVENT = "op:dag-nodes";

const _undo: Record<string, string[]> = Object.create(null);
let _pending: "delete" | "restore" | null = null;
let _last: { sessionId: string; opId: string; count: number } | null = null;
let _lastTimer: ReturnType<typeof setTimeout> | null = null;

/** The action bar's "Deleted N nodes · Undo" line, while it applies. */
export function lastDeletion(): { count: number } | null {
  if (!_last || _last.sessionId !== runtimeState.currentSessionId) return null;
  return { count: _last.count };
}

export function deletionPending(): boolean {
  return _pending !== null;
}

export function canUndoDeletion(): boolean {
  const sid = runtimeState.currentSessionId;
  return !!sid && !!_undo[sid]?.length;
}

function isStructure(n: GNode): boolean {
  const r = n as Record<string, unknown>;
  return n.display === "root"
    || r.covers_ids != null
    || !!r.superseded_summary
    || n.function === "attach"
    || n.function === "merge";
}

/** The raw row ids a selection of glyphs stands for. ``skipped`` counts
 *  glyphs that are structure, not content. */
export function deletionTargets(
  selected: readonly string[],
  graph: GNode[] = _lastGraph || [],
  anchorOf: ((id: string) => string) | null = _lastThreadModel?.anchorOf ?? null,
): { ids: string[]; skipped: number } {
  const byId: Record<string, GNode> = Object.create(null);
  graph.forEach((n) => { byId[String(n.id)] = n; });
  const ids = new Set<string>();
  let skipped = 0;
  selected.forEach((id) => {
    const n = byId[id];
    if (!n || isStructure(n)) {
      skipped++;
      return;
    }
    if (isSpawnRoot(n)) {
      // The square is the agent's run; the row that owns it is the call
      // that spawned it — deleting that takes the branch with it.
      const caller = n.caller ? String(n.caller) : "";
      if (caller && caller !== "ROOT" && byId[caller]) ids.add(caller);
      else skipped++;
      return;
    }
    ids.add(id);
  });
  if (anchorOf) {
    // Rows merged into a selected glyph (followup replies) go with it.
    graph.forEach((n) => {
      const id = String(n.id);
      if (ids.has(id)) return;
      const a = anchorOf(id);
      if (a !== id && ids.has(a) && byId[a] && !isSpawnRoot(byId[a])) ids.add(id);
    });
  }
  return { ids: Array.from(ids), skipped };
}

function send(payload: Record<string, unknown>): boolean {
  const sock = getSocket();
  if (!sock || sock.readyState !== WebSocket.OPEN) return false;
  sock.send(JSON.stringify(payload));
  return true;
}

function toastError(en: string, zh: string): void {
  void import("@/lib/format-utils/toast").then(({ showToast }) => {
    showToast(translateText(en, zh), { tone: "error" });
  });
}

/** Delete what is selected. */
export function deleteSelection(): void {
  const sid = runtimeState.currentSessionId;
  if (!sid || _pending) return;
  const { ids, skipped } = deletionTargets(selectedIds());
  if (!ids.length) {
    if (skipped) {
      toastError(
        "The root, summaries and merge points cannot be deleted.",
        "根节点、压缩摘要和合并点不能删除。",
      );
    }
    return;
  }
  if (!send({ action: "delete_nodes", session_id: sid, node_ids: ids })) return;
  _pending = "delete";
  emit();
}

/** Undo the latest delete in this session. */
export function undoDeletion(): void {
  const sid = runtimeState.currentSessionId;
  if (!sid || _pending) return;
  const opId = _undo[sid]?.[_undo[sid].length - 1];
  if (!opId) return;
  if (!send({ action: "restore_deleted_nodes", session_id: sid, op_id: opId })) return;
  _pending = "restore";
  emit();
}

function showLast(sessionId: string, opId: string, count: number): void {
  _last = { sessionId, opId, count };
  if (_lastTimer) clearTimeout(_lastTimer);
  _lastTimer = setTimeout(() => {
    _last = null;
    _lastTimer = null;
    emit();
  }, 8000);
}

function onFrame(type: string, d: Record<string, unknown>): void {
  const sid = String(d.session_id || "");
  const opId = String(d.op_id || "");
  _pending = null;
  if (type === "nodes_deleted") {
    if (d.ok && opId) {
      (_undo[sid] = _undo[sid] || []).push(opId);
      if (sid === runtimeState.currentSessionId) clearSelection();
      showLast(sid, opId, Number(d.deleted) || 0);
    } else {
      const err = String(d.error || "unknown error");
      toastError(`Delete failed: ${err}`, `删除失败：${err}`);
    }
  } else if (type === "nodes_restored") {
    const stack = _undo[sid] || [];
    // Undone, or no longer undoable (trash expired): either way it is
    // off the stack, so the next ⌘Z reaches the delete before it.
    const i = stack.lastIndexOf(opId);
    if (i >= 0) stack.splice(i, 1);
    if (_last && _last.opId === opId) _last = null;
    if (!d.ok) {
      const err = String(d.error || "unknown error");
      toastError(`Undo failed: ${err}`, `撤销失败：${err}`);
    }
  }
  emit();
}

if (typeof window !== "undefined") {
  window.addEventListener(DAG_NODES_EVENT, (e) => {
    const detail = (e as CustomEvent<{ type: string; data: Record<string, unknown> }>).detail;
    if (detail?.type) onFrame(detail.type, detail.data || {});
  });
}
