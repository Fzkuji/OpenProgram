"use client";

/**
 * Branches panel — React port of `conversations.js::renderBranchesPanel`.
 *
 * The right-rail list of a conversation's DAG branches: collapsed shows
 * just the active (HEAD) branch, expanded shows all. Each row can be
 * checked out (click), renamed (inline) or deleted.
 *
 * Branch data still comes from the legacy `window._branchesByConv`
 * cache (filled by the `branches_list` WS handler). This component
 * re-reads it on a `branches-updated` window event, which the legacy
 * `renderBranchesPanel` shim now dispatches. The cache + `fetchBranches`
 * migrate with the WS layer (slice E).
 */
import { useEffect, useRef, useState } from "react";

import { useSessionStore } from "@/lib/session-store";

interface BranchRow {
  head_msg_id: string;
  name?: string;
  active?: boolean;
}

interface ConvSummary {
  id: string;
  title?: string;
  channel?: string | null;
  account_id?: string | null;
}

interface BranchWindow {
  ws?: WebSocket;
  _branchesByConv?: Record<string, BranchRow[]>;
  _branchLaneColorMap?: Record<string, string>;
  conversations?: Record<string, ConvSummary>;
}

// Fallback palette — kept in sync with history-graph.ts LANE_COLORS.
// Normally the per-branch colour comes from `_branchLaneColorMap`.
const LANE_COLORS = [
  "#4f8ef7", "#5aad4e", "#d4843a", "#9d6fe0", "#e0445a", "#2db3d5",
  "#e0b020", "#35b89a", "#e066b3", "#6b8dd6", "#8fbf3f", "#d9694f",
  "#52c4c4", "#b08be0", "#c79a4a", "#e08a3a", "#6fae6f", "#d05fa0",
];

function wsSend(payload: unknown): void {
  const w = window as unknown as BranchWindow;
  if (w.ws && w.ws.readyState === WebSocket.OPEN) {
    w.ws.send(JSON.stringify(payload));
  }
}

const RENAME_SVG = (
  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M11.5 2.5l2 2L5 13l-3 1 1-3 8.5-8.5z" />
  </svg>
);
const DEL_SVG = (
  <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
    <line x1="2" y1="2" x2="8" y2="8" />
    <line x1="8" y1="2" x2="2" y2="8" />
  </svg>
);

function BranchItem({
  branch,
  color,
  sessionId,
  collapsed,
  selected,
  isBase,
  running,
  finishing,
  onToggleSelect,
  onSetBase,
}: {
  branch: BranchRow;
  color: string;
  sessionId: string;
  collapsed: boolean;
  selected: boolean;
  isBase: boolean;
  running: boolean;
  finishing: boolean;
  onToggleSelect: (headId: string, e: React.MouseEvent) => void;
  onSetBase: (headId: string, e: React.MouseEvent) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(branch.name || "");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  const isPending = branch.head_msg_id.startsWith("__pending_task__:");

  function commitRename() {
    setEditing(false);
    if (isPending) {
      setValue(branch.name || "");
      return;
    }
    const trimmed = value.trim();
    if (trimmed && trimmed !== (branch.name || "")) {
      wsSend({
        action: "rename_branch",
        session_id: sessionId,
        head_msg_id: branch.head_msg_id,
        name: trimmed,
      });
    } else {
      setValue(branch.name || "");
    }
  }

  function checkout() {
    if (editing || branch.active || isPending) return;
    wsSend({
      action: "checkout_branch",
      session_id: sessionId,
      head_msg_id: branch.head_msg_id,
    });
    wsSend({ action: "load_session", session_id: sessionId });
  }

  function del(e: React.MouseEvent) {
    e.stopPropagation();
    if (isPending) return;
    if (
      !window.confirm(
        "Delete this branch and its messages? This cannot be undone.",
      )
    )
      return;
    wsSend({
      action: "delete_branch",
      session_id: sessionId,
      head_msg_id: branch.head_msg_id,
    });
    wsSend({ action: "load_session", session_id: sessionId });
  }

  if (collapsed && !branch.active) return null;

  const cls = "branch-item"
    + (branch.active ? " active" : "")
    + (selected ? " selected" : "")
    + (isBase ? " base" : "")
    + (running ? " is-running" : "")
    + (finishing ? " is-finishing" : "");

  return (
    <div
      className={cls}
      data-head={branch.head_msg_id}
      onClick={checkout}
    >
      <span
        className="branch-item-check"
        title={
          isPending
            ? "Task is running — wait for it to finish before merging"
            : (selected
                ? "Click again to deselect; ⌘-click to mark as base"
                : "Select for merge (⌘-click to mark as base)")
        }
        onClick={(e) => {
          if (isPending) {
            e.stopPropagation();
            return;
          }
          if (e.metaKey || e.ctrlKey) onSetBase(branch.head_msg_id, e);
          else onToggleSelect(branch.head_msg_id, e);
        }}
      >
        {isBase ? "★" : selected ? "✓" : ""}
      </span>
      <span className="branch-item-dot" style={{ background: color }} />
      {editing ? (
        <input
          ref={inputRef}
          className="branch-item-name"
          style={{
            width: "100%",
            boxSizing: "border-box",
            font: "inherit",
            color: "var(--text-bright)",
            background: "var(--bg-input, rgba(255,255,255,0.06))",
            border: "1px solid var(--accent-blue, #6cb4ff)",
            borderRadius: 4,
            padding: "2px 6px",
            outline: "none",
          }}
          value={value}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => setValue(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commitRename();
            } else if (e.key === "Escape") {
              e.preventDefault();
              setValue(branch.name || "");
              setEditing(false);
            }
          }}
        />
      ) : (
        <span className="branch-item-name">{branch.name}</span>
      )}
      {branch.active ? <span className="branch-item-badge">HEAD</span> : null}
      {isPending ? (
        <span
          className="branch-item-badge"
          style={{ background: "rgba(160, 107, 255, 0.18)" }}
        >
          running
        </span>
      ) : null}
      <span className="branch-item-actions">
        <span
          className="branch-item-action branch-item-rename"
          title="Rename branch"
          onClick={(e) => {
            e.stopPropagation();
            setValue(branch.name || "");
            setEditing(true);
          }}
        >
          {RENAME_SVG}
        </span>
        <span
          className="branch-item-action branch-item-del"
          title="Delete branch"
          onClick={del}
        >
          {DEL_SVG}
        </span>
      </span>
    </div>
  );
}

// Per-session map of task_id → {target_head, status} mirrored from
// the ``op:task-status`` window event. We keep tasks in non-terminal
// state ('queued' / 'running') in the map so the panel renders a
// branch as 'running'; when a terminal status arrives we flip the
// branch to 'finishing' for ~1.2s (matches the convFinishingWipe
// keyframe) before dropping it. Implementation lives inside the
// component so the state survives across panel mounts.
interface TaskStatusDetail {
  task_id?: string;
  session_id?: string;
  target_branch_head_id?: string | null;
  head_id?: string | null;
  status?: string;
  label?: string | null;
  subject?: string | null;
}

// Synthetic prefix for "pending branch" rows the panel renders while
// the task is in flight but no real assistant_msg_id exists yet.
// Distinct from real DAG ids (12 hex chars) so the click handlers
// can short-circuit safely.
const PENDING_HEAD_PREFIX = "__pending_task__:";

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
        <div
          className="branches-merge-modal-backdrop"
          onClick={() => setMerging(false)}
        >
          <div
            className="branches-merge-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="branches-merge-modal-title">
              Merge {selected.length} branches
            </div>
            <div className="branches-merge-mode">
              <label className="branches-merge-mode-row">
                <input
                  type="radio"
                  name="merge-mode"
                  checked={!baseHead}
                  onChange={() => setBaseHead(null)}
                />
                <span>
                  <strong>Equal merge</strong> — write a new turn whose
                  parents are all selected branches. Reply lands as a
                  fresh branch tip.
                </span>
              </label>
              <label className="branches-merge-mode-row">
                <input
                  type="radio"
                  name="merge-mode"
                  checked={!!baseHead}
                  onChange={() => {
                    // Default base to first selected if nothing is
                    // ⌘-clicked yet — the radio is only useful when
                    // a base exists.
                    if (!baseHead && selected.length > 0) {
                      setBaseHead(selected[0]);
                    }
                  }}
                />
                <span>
                  <strong>Attach into ★ base</strong> — reply continues
                  the ★ branch, the other selections feed in as
                  context. ⌘-click a row to pick the base.
                </span>
              </label>
            </div>
            <textarea
              className="branches-merge-modal-input"
              placeholder="Optional instruction for the merge agent (how to reconcile)"
              value={mergeInstruction}
              onChange={(e) => setMergeInstruction(e.target.value)}
              autoFocus
              rows={4}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                  e.preventDefault();
                  runMerge();
                }
              }}
            />
            <div className="branches-merge-modal-hint">
              ⌘/Ctrl + Enter to merge
            </div>
            <div className="branches-merge-modal-actions">
              <button
                type="button"
                className="branches-merge-cancel"
                onClick={() => setMerging(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="branches-merge-go"
                onClick={runMerge}
              >
                Merge
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
