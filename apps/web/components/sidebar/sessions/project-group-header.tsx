"use client";

import { Plus } from "lucide-react";
import { SolarIcon } from "@/components/solar-icons";
import { activateOnKey } from "@/lib/utils";
import { sidebarNavIconClass, sidebarNavItemClass, sidebarNavLabelClass, sidebarProjectActionClass, SIDEBAR_ROW_ACTION_ICON } from "../nav-classes";
import styles from "../sidebar.module.css";

/* ---- project group header (Group-by → Project mode) --------------
 * The EXACT nav-row recipe the Functions/Chats links use: `ui-list-item`
 * box (32px) via sidebarNavItemClass, a folder in the standard 16px
 * icon slot that is open while the group is expanded and closed while it
 * is collapsed (the row's only expand/collapse cue), normal-weight label
 * — plus the new-session ＋ and the options ⋯ at the row end, the same
 * size and right edge as a chat row's ⋯. The project's path lives in the
 * row's title tooltip. */
export function ProjectGroupHeader({
  name,
  path,
  collapsed,
  onToggle,
  onNewSession,
  newSessionTitle,
  dragProps,
  onMove,
  reorderHint,
  icon, menuTrigger,
}: {
  name: string;
  path: string;
  collapsed: boolean;
  onToggle: () => void;
  onNewSession: () => void;
  newSessionTitle: string;
  dragProps: React.HTMLAttributes<HTMLDivElement>;
  onMove: (direction: -1 | 1) => void;
  reorderHint: string;
  icon?: string;
  menuTrigger: React.ReactNode;
}) {
  return (
    <div
      {...dragProps}
      className={sidebarNavItemClass + " " + styles.projectHeader + " " + styles.reflow + " select-none cursor-grab active:cursor-grabbing"}
      data-project-header=""
      role="button"
      tabIndex={0}
      aria-expanded={!collapsed}
      title={[path, reorderHint].filter(Boolean).join("\n")}
      aria-keyshortcuts="Alt+ArrowUp Alt+ArrowDown"
      onClick={(event) => {
        dragProps.onClick?.(event);
        if (!event.defaultPrevented) onToggle();
      }}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.altKey && (event.key === "ArrowUp" || event.key === "ArrowDown")) {
          event.preventDefault();
          onMove(event.key === "ArrowUp" ? -1 : 1);
        } else activateOnKey(onToggle)(event);
      }}
    >
      <span className={sidebarNavIconClass}>
        {/* The folder opens and closes with the group — the only
            expand/collapse cue on the row. A custom icon stays as set. */}
        {icon ? <span className="truncate" aria-hidden="true">{icon}</span>
          : <SolarIcon name={collapsed ? "folder" : "folder-open"} size={20} motionPreset="none" className="text-[var(--folder-color)]" aria-hidden="true" />}
      </span>
      <span className={sidebarNavLabelClass}>{name}</span>
      {/* -mr-[4px]: the buttons end 4px inside the row, where a chat
          row's ⋯ sits, so both columns line up. */}
      <div className="-mr-[4px] flex shrink-0 items-center gap-[2px]">
        <button
          type="button"
          title={newSessionTitle}
          aria-label={newSessionTitle}
          onClick={(e) => {
            e.stopPropagation();
            onNewSession();
          }}
          className={sidebarProjectActionClass + " text-text-muted opacity-0 transition-opacity duration-150 group-hover:opacity-100 hover:text-text-bright"}
        >
          <Plus size={SIDEBAR_ROW_ACTION_ICON} strokeWidth={2} />
        </button>
        {menuTrigger}
      </div>
    </div>
  );
}
