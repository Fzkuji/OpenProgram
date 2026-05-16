"use client";

/**
 * Branch menu — React port of `conversations.js::openBranchDropdown`.
 *
 * The topbar branch-chip dropdown: the conversation's DAG branches with
 * per-row checkout (click), inline rename and delete. Same actions as
 * the right-rail <BranchesPanel />, different surface.
 *
 * On open it force-refreshes the branch list (`fetchBranches`) so a
 * fresh retry/edit leaf shows up. Branch data still rides the legacy
 * `window._branchesByConv` cache + `window.fetchBranches` — they
 * migrate with the WS layer.
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useSessionStore } from "@/lib/session-store";

interface BranchRow {
  head_msg_id: string;
  name?: string;
  active?: boolean;
  is_named?: boolean;
}

interface BranchWindow {
  ws?: WebSocket;
  _branchesByConv?: Record<string, BranchRow[]>;
  fetchBranches?: (sid: string) => Promise<BranchRow[]>;
}

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

function BranchRowItem({
  branch,
  sessionId,
  onClose,
}: {
  branch: BranchRow;
  sessionId: string;
  onClose: () => void;
}) {
  const [hovered, setHovered] = useState(false);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(branch.is_named ? branch.name || "" : "");
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
    if (trimmed && trimmed !== (branch.is_named ? branch.name : "")) {
      wsSend({
        action: "rename_branch",
        session_id: sessionId,
        head_msg_id: branch.head_msg_id,
        name: trimmed,
      });
    }
  }

  function checkout() {
    if (editing) return;
    wsSend({
      action: "checkout_branch",
      session_id: sessionId,
      head_msg_id: branch.head_msg_id,
    });
    wsSend({ action: "load_session", session_id: sessionId });
    onClose();
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
    onClose();
  }

  const btnStyle: React.CSSProperties = {
    position: "absolute",
    top: "50%",
    transform: "translateY(-50%)",
    width: 24,
    height: 24,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 4,
    color: "var(--text-muted)",
    cursor: "pointer",
  };

  return (
    <div
      className={"model-dd-item" + (branch.active ? " active" : "")}
      style={{ gap: 0, position: "relative", paddingRight: 64 }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={checkout}
    >
      {editing ? (
        <input
          ref={inputRef}
          style={{
            flex: "1 1 auto",
            minWidth: 0,
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
          placeholder="new branch name (empty = cancel)"
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => setValue(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commitRename();
            } else if (e.key === "Escape") {
              e.preventDefault();
              setEditing(false);
            }
          }}
        />
      ) : (
        <span
          className="model-dd-name"
          style={{ flex: "1 1 auto", minWidth: 0, maxWidth: 320 }}
        >
          {branch.name}
        </span>
      )}
      {branch.active && !hovered ? (
        <span
          className="cap-badge ctx branch-head"
          style={{
            position: "absolute",
            right: 8,
            top: "50%",
            transform: "translateY(-50%)",
            pointerEvents: "none",
            padding: "0 8px",
            height: 20,
            lineHeight: "20px",
            display: "inline-flex",
            alignItems: "center",
          }}
        >
          HEAD
        </span>
      ) : null}
      {hovered && !editing ? (
        <>
          <span
            className="branch-rename"
            title="Rename branch"
            style={{ ...btnStyle, right: 36 }}
            onClick={(e) => {
              e.stopPropagation();
              setValue(branch.is_named ? branch.name || "" : "");
              setEditing(true);
            }}
          >
            {RENAME_SVG}
          </span>
          <span
            className="branch-del"
            title="Delete this branch"
            style={{ ...btnStyle, right: 8 }}
            onClick={del}
          >
            {DEL_SVG}
          </span>
        </>
      ) : null}
    </div>
  );
}

export function BranchMenu({
  anchorRef,
  onClose,
}: {
  anchorRef: React.RefObject<HTMLElement | null>;
  onClose: () => void;
}) {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [rows, setRows] = useState<BranchRow[] | null>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const a = anchorRef.current;
    if (!a) return;
    const r = a.getBoundingClientRect();
    setPos({ left: r.left, top: r.bottom + 4 });
  }, [anchorRef]);

  useEffect(() => {
    const w = window as unknown as BranchWindow;
    if (!sessionId || !w.fetchBranches) {
      setRows([]);
      return;
    }
    // Force-refresh so a fresh retry/edit leaf shows up.
    if (w._branchesByConv) delete w._branchesByConv[sessionId];
    w.fetchBranches(sessionId).then(
      (r) => setRows(r || []),
      () => setRows([]),
    );
  }, [sessionId]);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      const t = e.target as Node | null;
      if (!t) return;
      if (panelRef.current?.contains(t)) return;
      if (anchorRef.current?.contains(t)) return;
      onClose();
    }
    const id = setTimeout(() => document.addEventListener("click", onDoc), 0);
    return () => {
      clearTimeout(id);
      document.removeEventListener("click", onDoc);
    };
  }, [anchorRef, onClose]);

  if (!pos || typeof document === "undefined") return null;

  return createPortal(
    <div
      ref={panelRef}
      className="agent-selector model-dropdown branch-selector"
      style={{
        position: "fixed",
        left: pos.left,
        top: pos.top,
        minWidth: 0,
        maxWidth: "none",
        width: "auto",
        boxShadow: "0 12px 32px rgba(0, 0, 0, 0.5)",
      }}
    >
      <div className="model-dd-group-label" style={{ paddingTop: 6 }}>
        <span>Branches</span>
      </div>
      {rows !== null && rows.length === 0 ? (
        <div className="model-dd-group-label" style={{ fontSize: 11 }}>
          <span>No branches yet — retry or edit a message to fork.</span>
        </div>
      ) : null}
      {(rows ?? []).map((b) => (
        <BranchRowItem
          key={b.head_msg_id}
          branch={b}
          sessionId={sessionId as string}
          onClose={onClose}
        />
      ))}
    </div>,
    document.body,
  );
}
