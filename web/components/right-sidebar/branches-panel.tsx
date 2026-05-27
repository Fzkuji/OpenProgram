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

interface BranchWindow {
  ws?: WebSocket;
  _branchesByConv?: Record<string, BranchRow[]>;
  _branchLaneColorMap?: Record<string, string>;
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
}: {
  branch: BranchRow;
  color: string;
  sessionId: string;
  collapsed: boolean;
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

  function commitRename() {
    setEditing(false);
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
    if (editing || branch.active) return;
    wsSend({
      action: "checkout_branch",
      session_id: sessionId,
      head_msg_id: branch.head_msg_id,
    });
    wsSend({ action: "load_session", session_id: sessionId });
  }

  function del(e: React.MouseEvent) {
    e.stopPropagation();
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

  return (
    <div
      className={"branch-item" + (branch.active ? " active" : "")}
      data-head={branch.head_msg_id}
      onClick={checkout}
    >
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

export function BranchesPanel() {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [collapsed, setCollapsed] = useState(true);
  const [, setTick] = useState(0);

  // Re-read the legacy branch cache whenever the WS branch handlers
  // signal an update (the legacy `renderBranchesPanel` shim dispatches
  // `branches-updated`).
  useEffect(() => {
    const bump = () => setTick((t) => t + 1);
    window.addEventListener("branches-updated", bump);
    return () => window.removeEventListener("branches-updated", bump);
  }, []);

  // Start collapsed again on every conversation change.
  useEffect(() => {
    setCollapsed(true);
  }, [sessionId]);

  const w = window as unknown as BranchWindow;
  const rows = (sessionId && w._branchesByConv?.[sessionId]) || [];
  if (!sessionId || rows.length === 0) return null;

  const graphColors = w._branchLaneColorMap || {};

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
          />
        ))}
      </div>
    </div>
  );
}
