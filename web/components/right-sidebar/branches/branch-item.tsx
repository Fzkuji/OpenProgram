"use client";

import { cloneElement, useEffect, useRef, useState } from "react";

import { useTranslation } from "@/lib/i18n";
import type { AnimatedNavIconHandle } from "@/components/animated-icons";

import {
  DEL_SVG,
  RENAME_SVG,
  wsSend,
  type BranchRow,
} from "./types";

/**
 * Single row in the branches panel — checkbox, dot, name (rename in
 * place), HEAD/running badges, and rename + delete actions. Pure
 * presentational; the parent owns selection / multi-select state.
 */
export function BranchItem({
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
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  // 0ms feedback (interaction-feedback policy): highlight this row as the
  // active branch the moment it's clicked, before the checkout WS +
  // load_session reload flips ``branch.active`` for real (~1 round-trip).
  // Cleared once the reload lands (real active arrives) or after a timeout
  // if the checkout never took.
  const [pendingActive, setPendingActive] = useState(false);
  const [value, setValue] = useState(branch.name || "");
  const inputRef = useRef<HTMLInputElement>(null);
  // Animated rename/delete glyphs driven by the WHOLE action button's
  // hover (24px), not the 13px glyph — same fix as the message actions.
  const renameIconRef = useRef<AnimatedNavIconHandle>(null);
  const delIconRef = useRef<AnimatedNavIconHandle>(null);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  // Drop the optimistic highlight once the server confirms this row is the
  // real active branch (reload landed), or if the parent switched the
  // active branch elsewhere.
  useEffect(() => {
    if (branch.active) setPendingActive(false);
  }, [branch.active]);

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
    setPendingActive(true);
    // Self-clear if the checkout never resolves (row still not active after
    // the round-trip window) so a stuck row doesn't lie about being active.
    setTimeout(() => setPendingActive(false), 10_000);
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
    if (!window.confirm(t("right.delete_branch_confirm"))) return;
    wsSend({
      action: "delete_branch",
      session_id: sessionId,
      head_msg_id: branch.head_msg_id,
    });
    wsSend({ action: "load_session", session_id: sessionId });
  }

  if (collapsed && !branch.active) return null;

  // Real active supersedes the optimistic flag — once the reload lands and
  // this row is genuinely the HEAD, drop the pending highlight.
  const effectiveActive = branch.active || pendingActive;

  const cls = "branch-item"
    + (effectiveActive ? " active" : "")
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
            ? t("right.task_running_merge_wait")
            : (selected
                ? t("right.deselect_base_hint")
                : t("right.select_merge_hint"))
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
      {branch.active ? <span className="branch-item-badge">{t("right.head")}</span> : null}
      {isPending ? (
        <span
          className="branch-item-badge"
          style={{ background: "rgba(160, 107, 255, 0.18)" }}
        >
          {t("right.running")}
        </span>
      ) : null}
      <span className="branch-item-actions">
        <span
          className="branch-item-action branch-item-rename"
          title={t("right.rename_branch")}
          onMouseEnter={() => renameIconRef.current?.startAnimation?.()}
          onMouseLeave={() => renameIconRef.current?.stopAnimation?.()}
          onClick={(e) => {
            e.stopPropagation();
            setValue(branch.name || "");
            setEditing(true);
          }}
        >
          {cloneElement(RENAME_SVG, { ref: renameIconRef } as Record<string, unknown>)}
        </span>
        <span
          className="branch-item-action branch-item-del"
          title={t("right.delete_branch")}
          onMouseEnter={() => delIconRef.current?.startAnimation?.()}
          onMouseLeave={() => delIconRef.current?.stopAnimation?.()}
          onClick={del}
        >
          {cloneElement(DEL_SVG, { ref: delIconRef } as Record<string, unknown>)}
        </span>
      </span>
    </div>
  );
}
