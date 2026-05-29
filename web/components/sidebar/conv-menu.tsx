"use client";

/**
 * ConvMenu — the right-click / ⋯ context menu for a Recents row.
 *
 * Presentational only: it renders the menu item list and the inline
 * "Move to group" sub-list, and calls back to the parent (ConvItem in
 * sessions-list.tsx) for every action. The Popover shell + open state
 * live in the parent so the same menu can be opened by either the ⋯
 * button or a right-click on the row.
 *
 * Items mirror Claude.ai's conversation menu, minus the ones we have
 * no backing infra for (Open in / Mark as read / Share). Each item
 * shows a right-aligned single-key shortcut; while the menu is open,
 * pressing that key fires the item (menu-local, see onKeyDown).
 */

import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import {
  MENU_PANEL,
  MENU_SEPARATOR,
  SHORTCUT,
  itemCls,
} from "@/components/chat/top-bar/menu-styles";

export interface ConvMenuConv {
  id: string;
  title?: string;
  pinned?: boolean;
  archived?: boolean;
  group?: string;
}

export interface ConvMenuProps {
  conv: ConvMenuConv;
  /** Existing group names across all conversations, for the
   *  "Move to group" sub-list. */
  groups: string[];
  onRename: () => void;
  onTogglePin: () => void;
  onToggleArchive: () => void;
  /** "" ungroups; any other string assigns/creates that group. */
  onMoveToGroup: (group: string) => void;
  onNewGroup: () => void;
  onCopyLink: () => void;
  onDelete: () => void;
  onClose: () => void;
}

export function ConvMenu({
  conv,
  groups,
  onRename,
  onTogglePin,
  onToggleArchive,
  onMoveToGroup,
  onNewGroup,
  onCopyLink,
  onDelete,
  onClose,
}: ConvMenuProps) {
  const { t } = useTranslation();
  const [groupOpen, setGroupOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // Focus the menu on mount so menu-local key shortcuts work without
  // an extra click.
  useEffect(() => {
    rootRef.current?.focus();
  }, []);

  function run(fn: () => void) {
    fn();
    onClose();
  }

  function onKeyDown(e: React.KeyboardEvent) {
    // Single-key shortcuts mirror Claude's menu. Ignore when a
    // modifier is held so they don't hijack browser shortcuts.
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k === "r") { e.preventDefault(); run(onRename); }
    else if (k === "p") { e.preventDefault(); run(onTogglePin); }
    else if (k === "c") { e.preventDefault(); run(onCopyLink); }
    else if (k === "a") { e.preventDefault(); run(onToggleArchive); }
    else if (k === "d") { e.preventDefault(); run(onDelete); }
    else if (k === "escape") { e.preventDefault(); onClose(); }
  }

  return (
    <div
      ref={rootRef}
      tabIndex={-1}
      onKeyDown={onKeyDown}
      className={`${MENU_PANEL} min-w-[200px] outline-none`}
    >
      <MenuItem label={t("sidebar.rename")} shortcut="R" onClick={() => run(onRename)} />
      <MenuItem
        label={conv.pinned ? t("sidebar.unpin") : t("sidebar.pin")}
        shortcut="P"
        onClick={() => run(onTogglePin)}
      />

      {/* Move to group — inline-expanding sub-list (Radix popover has
          no native submenu flyout). */}
      <button
        type="button"
        className={itemCls(false)}
        onClick={() => setGroupOpen((v) => !v)}
        aria-expanded={groupOpen}
      >
        <span className="flex-1 text-left">{t("sidebar.move_to_group")}</span>
        <span className="flex w-[16px] shrink-0 items-center justify-center text-text-muted">{groupOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
      </button>
      {groupOpen && (
        <div className="flex flex-col border-l border-[var(--border)] ml-3 my-0.5">
          {conv.group ? (
            <SubItem
              label={t("sidebar.remove_from_group")}
              onClick={() => run(() => onMoveToGroup(""))}
            />
          ) : null}
          {groups
            .filter((g) => g && g !== conv.group)
            .map((g) => (
              <SubItem key={g} label={g} onClick={() => run(() => onMoveToGroup(g))} />
            ))}
          <SubItem
            label={t("sidebar.new_group")}
            accent
            onClick={() => run(onNewGroup)}
          />
        </div>
      )}

      <MenuItem label={t("sidebar.copy_link")} shortcut="C" onClick={() => run(onCopyLink)} />
      <MenuItem
        label={conv.archived ? t("sidebar.unarchive") : t("sidebar.archive")}
        shortcut="A"
        onClick={() => run(onToggleArchive)}
      />

      <div className={MENU_SEPARATOR} />

      <MenuItem
        label={t("sidebar.delete")}
        shortcut="D"
        danger
        onClick={() => run(onDelete)}
      />
    </div>
  );
}

/* ---- item primitives ------------------------------------------- */

// MenuItem / SubItem share the canonical `itemCls` from menu-styles, so
// this context menu is pixel-identical to the topbar pickers. Danger
// (Delete) and the orange "new group" accent are the only per-item
// variants; the shortcut hint uses the shared SHORTCUT style.
function MenuItem({
  label,
  shortcut,
  danger,
  onClick,
}: {
  label: string;
  shortcut?: string;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button type="button" onClick={onClick} className={itemCls(false, danger)}>
      <span className="flex-1 text-left">{label}</span>
      {shortcut ? (
        <span className={SHORTCUT + " w-[16px] text-center"}>{shortcut}</span>
      ) : null}
    </button>
  );
}

function SubItem({
  label,
  accent,
  onClick,
}: {
  label: string;
  accent?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={itemCls(false) + (accent ? " !text-[var(--accent-orange)]" : "")}
    >
      <span className="flex-1 truncate text-left">{label}</span>
    </button>
  );
}
