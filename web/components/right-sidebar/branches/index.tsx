"use client";

/**
 * Branches panel — React port of `conversations.js::renderBranchesPanel`.
 *
 * The right-rail list of a conversation's DAG branches: collapsed shows
 * just the active (HEAD) branch, expanded shows all. Each row can be
 * checked out (click), renamed (inline) or deleted.
 *
 * Originally a single 783-line file (branches-panel.tsx); now split
 * into types.tsx (shared types + helpers + SVG glyphs),
 * branch-item.tsx (single row), and this index.tsx (the panel itself).
 */
import { useEffect, useRef, useState } from "react";

import { useSessionStore } from "@/lib/session-store";

import { BranchItem } from "./branch-item";
import { MergeModal } from "./merge-modal";
import {
  LANE_COLORS,
  PENDING_HEAD_PREFIX,
  wsSend,
  type BranchRow,
  type BranchWindow,
  type ConvSummary,
  type TaskStatusDetail,
} from "./types";

export function BranchesPanel() {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [collapsed, setCollapsed] = useState(false);
  const [, setTick] = useState(0);
  // task_id → {targetHead, finalHead, status, label}. We resolve the
  // BranchItem.head_msg_id we want to animate from either
  // target_branch_head_id (set at spawn time, when no real branch
  // tip exists yet) OR head_id (set at completion time). If neither
  // is known the panel renders a synthetic placeholder row labeled
  // after the task so the user still sees a running animation
  // somewhere — see PENDING_HEAD_PREFIX.
  const [taskMap, setTaskMap] = useState<Record<string,
    { targetHead?: string | null; finalHead?: string | null;
      status: string; sessionId?: string; label?: string | null }>>({});
  // Branch head_msg_ids currently in "finishing" wipe — added on
  // terminal status, removed 1200ms later.
  const [finishingHeads, setFinishingHeads] = useState<Set<string>>(
    () => new Set(),
  );
  // Multi-select state for merging. ``selected`` is a list of
  // head_msg_ids the user has clicked the checkbox on; ``baseHead``
  // is the optional ⌘-clicked one that becomes ``base_peer`` (merge
  // reply continues that branch). Reset whenever the conversation
  // changes.
  const [selected, setSelected] = useState<string[]>([]);
  const [baseHead, setBaseHead] = useState<string | null>(null);
  const [merging, setMerging] = useState(false);
  const [mergeInstruction, setMergeInstruction] = useState("");
  // Attach-target picker. Open when the user clicks "Attach to" —
  // shows the list of branches that aren't currently selected, so
  // they can pick where the attach pointer lands. Picker scope can
  // be the current session (default) or any other session whose
  // branches the user wants to attach onto.
  const [attachOpen, setAttachOpen] = useState(false);
  const [pickerScope, setPickerScope] = useState<string | null>(null); // null = current session

  // Re-read the legacy branch cache whenever the WS branch handlers
  // signal an update (the legacy `renderBranchesPanel` shim dispatches
  // `branches-updated`).
  useEffect(() => {
    const bump = () => setTick((t) => t + 1);
    window.addEventListener("branches-updated", bump);
    return () => window.removeEventListener("branches-updated", bump);
  }, []);

  // Subscribe to async task status broadcasts. Each ``task_status``
  // event tells us which branch head_msg_id should animate as
  // running. Terminal statuses (completed / cancelled / errored)
  // flip the branch to ``finishing`` for the wipe keyframe and then
  // drop it from the map.
  useEffect(() => {
    const onTaskStatus = (e: Event) => {
      const ce = e as CustomEvent<TaskStatusDetail>;
      const d = ce.detail || {};
      const tid = d.task_id;
      if (!tid) return;
      const status = (d.status || "").toLowerCase();
      const terminal = (
        status === "completed"
        || status === "cancelled"
        || status === "errored"
      );
      const targetHead = d.target_branch_head_id || null;
      const finalHead = d.head_id || null;
      setTaskMap((cur) => {
        const next = { ...cur };
        if (terminal) {
          // Drop from running map; if we have a head we know which
          // branch row to wipe.
          const headForWipe = finalHead || cur[tid]?.finalHead
                              || targetHead || cur[tid]?.targetHead;
          delete next[tid];
          if (headForWipe) {
            setFinishingHeads((fs) => {
              const ns = new Set(fs);
              ns.add(headForWipe);
              return ns;
            });
            setTimeout(() => {
              setFinishingHeads((fs) => {
                if (!fs.has(headForWipe)) return fs;
                const ns = new Set(fs);
                ns.delete(headForWipe);
                return ns;
              });
            }, 1200);
          }
          return next;
        }
        // Non-terminal — record / update.
        next[tid] = {
          targetHead, finalHead, status,
          sessionId: d.session_id,
          label: d.label || d.subject || null,
        };
        return next;
      });
    };
    window.addEventListener("op:task-status", onTaskStatus as EventListener);
    return () => {
      window.removeEventListener("op:task-status", onTaskStatus as EventListener);
    };
  }, []);

  // Hydrate from server on session change so a refresh mid-task
  // restores the running animation. We send ``list_tasks`` and let
  // the response come back via ``op:task-message``.
  useEffect(() => {
    if (!sessionId) return;
    wsSend({
      action: "list_tasks",
      session_id: sessionId,
      status_filter: ["pending", "queued", "running"],
    });
    const onMsg = (e: Event) => {
      const ce = e as CustomEvent<{ type: string; data: Record<string, unknown> }>;
      const det = ce.detail;
      if (!det) return;
      if (det.type !== "tasks_list") return;
      const tasks = (det.data?.tasks as Array<Record<string, unknown>>) || [];
      setTaskMap(() => {
        const m: Record<string, {
          targetHead?: string | null; finalHead?: string | null;
          status: string; sessionId?: string; label?: string | null;
        }> = {};
        for (const t of tasks) {
          const tid = t.id as string | undefined;
          const status = (t.status as string | undefined) || "";
          if (!tid) continue;
          if (status === "completed" || status === "cancelled"
              || status === "errored") continue;
          m[tid] = {
            targetHead: (t.target_branch_head_id as string | null) || null,
            finalHead: (t.head_id as string | null) || null,
            status,
            sessionId: (t.parent_session_id as string | undefined),
            label: (t.label as string | null)
                   || (t.subject as string | null) || null,
          };
        }
        return m;
      });
    };
    window.addEventListener("op:task-message", onMsg as EventListener);
    return () => {
      window.removeEventListener("op:task-message", onMsg as EventListener);
    };
  }, [sessionId]);

  // Reset transient state on every conversation change. The panel
  // stays expanded by default — the right-rail already reserved a
  // slot for the branch list, no reason to make the user click
  // "Show" before they can see what branches exist.
  useEffect(() => {
    setSelected([]);
    setBaseHead(null);
    setMerging(false);
    setMergeInstruction("");
    setAttachOpen(false);
    setPickerScope(null);
  }, [sessionId]);

  // When the user opens the picker on a non-current session, request
  // that session's branches so they show up in the dropdown.
  useEffect(() => {
    if (!attachOpen || !pickerScope) return;
    const ww = window as unknown as BranchWindow;
    if (ww._branchesByConv?.[pickerScope]) return;
    wsSend({ action: "list_branches", session_id: pickerScope });
  }, [attachOpen, pickerScope]);

  function toggleSelect(headId: string, e: React.MouseEvent) {
    e.stopPropagation();
    setSelected((s) => {
      if (s.includes(headId)) {
        // Deselect; also clear base if it was this row.
        if (baseHead === headId) setBaseHead(null);
        return s.filter((h) => h !== headId);
      }
      return [...s, headId];
    });
  }

  function setBase(headId: string, e: React.MouseEvent) {
    e.stopPropagation();
    setBaseHead((b) => (b === headId ? null : headId));
    setSelected((s) => (s.includes(headId) ? s : [...s, headId]));
  }

  function runMerge() {
    if (!sessionId || selected.length < 2) return;
    const peers = selected.map((head_id) => ({
      session_id: sessionId, head_id,
    }));
    const base_peer = baseHead ? selected.indexOf(baseHead) : null;
    wsSend({
      action: "merge_branches",
      session_id: sessionId,
      peers,
      message: mergeInstruction.trim(),
      base_peer,
    });
    setMerging(false);
    setMergeInstruction("");
    setSelected([]);
    setBaseHead(null);
    // Merge runs the LLM server-side (seconds), so a 100ms
    // load_session would pull the pre-merge state. The backend
    // broadcasts ``session_reload`` once the merge turn lands — the
    // global ws message handler picks it up and re-fetches the
    // conversation. Nothing to do here.
  }

  function runAttachTo(anchorHeadId: string, anchorSessionId?: string) {
    if (!sessionId || selected.length === 0 || !anchorHeadId) return;
    const anchorSid = anchorSessionId || sessionId;
    // For each selected source branch, write an attach pointer
    // anchored at the user-picked branch. N selected = N pointers
    // landing on the same anchor. Source is always currentSessionId;
    // anchor may be the same session (in-session attach) or another
    // session (cross-session attach).
    for (const src of selected) {
      if (anchorSid === sessionId && src === anchorHeadId) continue;   // self-attach
      wsSend({
        action: "attach_branch",
        session_id: sessionId,
        target_head_msg_id: src,
        anchor_session_id: anchorSid,
        anchor_head_msg_id: anchorHeadId,
      });
    }
    setSelected([]);
    setBaseHead(null);
    setAttachOpen(false);
    setPickerScope(null);
    // session_reload broadcast picks up the new attach cards.
  }

  const w = window as unknown as BranchWindow;
  const realRows = (sessionId && w._branchesByConv?.[sessionId]) || [];

  // Resolve which branch head_msg_ids should animate as "running".
  // A task in non-terminal state maps to a branch via either
  // target_branch_head_id (set at spawn) or head_id (set when the
  // task lands its assistant reply). Only consider tasks for the
  // current session. Tasks without any head yet get a synthetic
  // pending row so the user sees the animation immediately.
  const runningHeads = new Set<string>();
  const pendingRows: BranchRow[] = [];
  for (const tid in taskMap) {
    const entry = taskMap[tid];
    if (entry.sessionId && entry.sessionId !== sessionId) continue;
    let head: string | null = null;
    if (entry.finalHead) {
      head = entry.finalHead;
    } else if (entry.targetHead) {
      head = entry.targetHead;
    }
    if (head) {
      runningHeads.add(head);
    } else {
      // No real head id yet — synthesize one keyed off task_id so
      // the row stays stable across status events.
      const synth = `${PENDING_HEAD_PREFIX}${tid}`;
      runningHeads.add(synth);
      pendingRows.push({
        head_msg_id: synth,
        name: entry.label || `task ${tid.slice(0, 6)}`,
        active: false,
      });
    }
  }
  const rows: BranchRow[] = pendingRows.length
    ? [...realRows, ...pendingRows] : realRows;

  // Paint the same is-running class onto the history-graph SVG
  // nodes so the DAG mini-map mirrors the panel's running animation
  // (the panel uses CSS pulses on .branch-item; history-graph reads
  // the same class on .history-node). Use a MutationObserver so the
  // class survives history-graph re-renders. Keyed off a stable
  // string snapshot of runningHeads to keep the effect's deps array
  // shallow.
  const runningKey = Array.from(runningHeads).sort().join("|");
  useEffect(() => {
    const ids = runningKey ? new Set(runningKey.split("|")) : new Set<string>();
    function paint() {
      document.querySelectorAll<SVGGElement>(
        ".history-node[data-msg-id]",
      ).forEach((n) => {
        const id = n.getAttribute("data-msg-id") || "";
        n.classList.toggle("is-running", id !== "" && ids.has(id));
      });
    }
    paint();
    const body = document.querySelector(".history-body");
    if (!body) return;
    const obs = new MutationObserver(paint);
    obs.observe(body, { childList: true, subtree: true });
    return () => obs.disconnect();
  }, [runningKey]);

  if (!sessionId || rows.length === 0) return null;

  const graphColors = w._branchLaneColorMap || {};
  // Picker scope: null = current session, otherwise that session id.
  const pickerSid = pickerScope || sessionId;
  const pickerRows = w._branchesByConv?.[pickerSid] || [];
  // Targets the "Attach to" picker offers. Filter out selected source
  // branches only when we're showing the current session — selected
  // branches always live in the current session, so cross-session
  // candidates aren't subject to that filter.
  const attachCandidates = pickerSid === sessionId
    ? pickerRows.filter((r) => !selected.includes(r.head_msg_id))
    : pickerRows;
  // Other sessions, for the picker's scope switcher. Sort by title
  // so the order is predictable.
  const allConvs = w.conversations || {};
  const otherSessions = Object.values(allConvs)
    .filter((c) => c.id && c.id !== sessionId)
    .sort((a, b) => (a.title || a.id).localeCompare(b.title || b.id));

  return (
    <div className={"branches-section" + (collapsed ? " is-collapsed" : "")}>
      <div
        className="sidebar-section-header"
        onClick={() => setCollapsed((c) => !c)}
      >
        <span className="sidebar-section-title">Branches</span>
        <span className="sidebar-section-hint">
          {collapsed ? "Show" : "Hide"}
        </span>
      </div>
      <div className="branches-list">
        {rows.map((b, i) => (
          <BranchItem
            key={b.head_msg_id}
            branch={b}
            color={
              graphColors[b.head_msg_id] ||
              LANE_COLORS[i % LANE_COLORS.length]
            }
            sessionId={sessionId}
            collapsed={collapsed}
            selected={selected.includes(b.head_msg_id)}
            isBase={baseHead === b.head_msg_id}
            running={runningHeads.has(b.head_msg_id)}
            finishing={finishingHeads.has(b.head_msg_id)}
            onToggleSelect={toggleSelect}
            onSetBase={setBase}
          />
        ))}
      </div>
      {!collapsed && selected.length >= 1 ? (
        <div className="branches-merge-bar">
          <span className="branches-merge-summary">
            {selected.length} selected
            {selected.length >= 2 && baseHead ? " · ★ base" : ""}
          </span>
          <div className="branches-attach-wrap">
            <button
              type="button"
              className="branches-merge-btn"
              onClick={() => setAttachOpen((v) => !v)}
              title="Attach selected branch(es) to another branch"
            >
              Attach to ▾
            </button>
            {attachOpen ? (
              <div
                className="branches-attach-picker"
                onClick={(e) => e.stopPropagation()}
              >
                {/* Scope switcher — pick which session's branches the
                    picker is listing. Default = current session;
                    selecting another session triggers a list_branches
                    fetch (see effect above). */}
                <div className="branches-attach-scope">
                  <button
                    type="button"
                    className={
                      "branches-attach-scope-btn"
                      + (pickerScope === null ? " is-active" : "")
                    }
                    onClick={() => setPickerScope(null)}
                  >
                    This session
                  </button>
                  {otherSessions.length > 0 ? (
                    <select
                      className="branches-attach-scope-select"
                      value={pickerScope || ""}
                      onChange={(e) => setPickerScope(e.target.value || null)}
                    >
                      <option value="">Other session…</option>
                      {otherSessions.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.title || c.id.slice(0, 12)}
                        </option>
                      ))}
                    </select>
                  ) : null}
                </div>
                {attachCandidates.length === 0 ? (
                  <div className="branches-attach-picker-empty">
                    {pickerScope === null
                      ? "No other branches in this session."
                      : "Loading…"}
                  </div>
                ) : (
                  attachCandidates.map((b) => (
                    <button
                      key={b.head_msg_id}
                      type="button"
                      className="branches-attach-picker-item"
                      onClick={() => runAttachTo(b.head_msg_id, pickerSid)}
                      title={b.head_msg_id}
                    >
                      {b.name || b.head_msg_id.slice(0, 8)}
                    </button>
                  ))
                )}
              </div>
            ) : null}
          </div>
          {selected.length >= 2 ? (
            <button
              type="button"
              className="branches-merge-btn"
              onClick={() => {
                setAttachOpen(false);
                setMerging(true);
              }}
            >
              Merge…
            </button>
          ) : null}
          <button
            type="button"
            className="branches-merge-clear"
            onClick={() => {
              setSelected([]);
              setBaseHead(null);
              setAttachOpen(false);
            }}
            title="Clear selection"
          >
            ×
          </button>
        </div>
      ) : null}
      {merging ? (
        <MergeModal
          selected={selected}
          baseHead={baseHead}
          mergeInstruction={mergeInstruction}
          setBaseHead={setBaseHead}
          setMergeInstruction={setMergeInstruction}
          onCancel={() => setMerging(false)}
          onMerge={runMerge}
        />
      ) : null}
    </div>
  );
}
