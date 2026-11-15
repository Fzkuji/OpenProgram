"use client";

/**
 * ConvMenu — the right-click / ⋯ context menu for a Recents row.
 *
 * Built on the shadcn (radix-nova) DropdownMenu: the parent (ConvItem in
 * conv-item.tsx) owns the DropdownMenu root, its open state and the
 * anchor at the click point, so the ⋯ button and a right-click on the row
 * open the same menu; this file renders the DropdownMenuContent. The
 * "Move to group" / "Move to project" / "Export" rows are DropdownMenuSub
 * submenus, so hover-to-open, the pointer grace on the way to a submenu
 * and keyboard navigation are Radix's own.
 *
 * Laid out like the project menu (project-menu.tsx): a muted icon on
 * every row and the rows grouped by purpose — this chat's name and pin,
 * where it lives, sharing it, archiving or deleting it. Items mirror
 * Claude.ai's conversation menu, minus the ones we have no backing infra
 * for (Open in / Mark as read / Share). While the menu is open, R / P /
 * C / A / D fire Rename / Pin / Copy link / Archive / Delete
 * (menu-local, see onKeyDown — it runs before Radix's typeahead, which
 * it replaces in the top-level menu); each row shows its key as a badge.
 * Rows are the shared menu row (components/ui/menu/menu-parts.tsx).
 */

import { MenuRow, type MenuGlyph, type MenuKeys } from "@/components/ui/menu/menu-parts";

import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import {
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu";
import {
  GROUP_LABEL,
  MENU_ITEM_STATES,
  MENU_PANEL,
  MENU_SEPARATOR,
  itemCls,
} from "@/components/ui/menu/menu-styles";

export interface ConvMenuConv {
  id: string;
  title?: string;
  pinned?: boolean;
  archived?: boolean;
  group?: string;
}

/** A project the chat can be moved into. */
export interface ConvMenuProject { id: string; name: string }

export interface ConvMenuProps {
  conv: ConvMenuConv;
  /** Existing group names across all conversations, for the
   *  "Move to group" sub-list. */
  groups: string[];
  /** The other projects, for the "Move to project" sub-list; the chat's
   *  own project is left out. Absent until the registry has answered. */
  projects?: ConvMenuProject[];
  onMoveToProject?: (projectId: string) => void;
  onRename: () => void;
  onAutoRename: () => void;
  onTogglePin: () => void;
  onToggleArchive: () => void;
  /** "" ungroups; any other string assigns/creates that group. */
  onMoveToGroup: (group: string) => void;
  onNewGroup: () => void;
  onCopyLink: () => void;
  /** Download the session as a file; the format picks the renderer. */
  onExport: (format: "md" | "html") => void;
  onDelete: () => void;
  onClose: () => void;
}

/** The DropdownMenuContent both menus share: opened at the click point,
 *  and kept from leaking clicks / right-clicks to the row behind it (the
 *  portal still bubbles through the React tree). */
function MenuContent({ onKeyDown, children }: {
  onKeyDown?: (e: React.KeyboardEvent<HTMLDivElement>) => void;
  children: React.ReactNode;
}) {
  return (
    <DropdownMenuContent
      align="start"
      side="bottom"
      sideOffset={4}
      onCloseAutoFocus={(e) => e.preventDefault()}
      onClick={(e) => e.stopPropagation()}
      onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); }}
      onKeyDown={onKeyDown}
      className={cn(MENU_PANEL, "min-w-[200px] text-text-primary")}
    >
      {children}
    </DropdownMenuContent>
  );
}

export function ConvMenu({
  conv,
  groups,
  onRename,
  onAutoRename,
  onTogglePin,
  onToggleArchive,
  onMoveToGroup,
  onNewGroup,
  projects,
  onMoveToProject,
  onCopyLink,
  onExport,
  onDelete,
  onClose,
}: ConvMenuProps) {
  const { t } = useTranslation();
  const otherGroups = groups.filter((g) => g && g !== conv.group);

  function run(fn: () => void) {
    fn();
    onClose();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    // Single-key shortcuts mirror Claude's menu. Ignore when a
    // modifier is held so they don't hijack browser shortcuts, and
    // inside a submenu (its keys bubble here through the React tree).
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (!e.currentTarget.contains(e.target as Node)) return;
    const action = ({ r: onRename, p: onTogglePin, c: onCopyLink, a: onToggleArchive, d: onDelete } as
      Record<string, () => void>)[e.key.toLowerCase()];
    if (action) { e.preventDefault(); run(action); }
  }

  return (
    <MenuContent onKeyDown={onKeyDown}>
      <MenuItem glyph="pen" label={t("sidebar.rename")} keys={{ key: "R" }} onSelect={onRename} />
      <MenuItem glyph="stars" label={t("sidebar.auto_rename")} onSelect={onAutoRename} />
      <MenuItem
        glyph="pin"
        label={conv.pinned ? t("sidebar.unpin") : t("sidebar.pin")}
        keys={{ key: "P" }}
        onSelect={onTogglePin}
      />

      <DropdownMenuSeparator className={MENU_SEPARATOR} />

      <Submenu glyph="layers" label={t("sidebar.move_to_group")}>
        {conv.group ? (
          <MenuItem glyph="close-circle" label={t("sidebar.remove_from_group")} onSelect={() => onMoveToGroup("")} />
        ) : null}
        {otherGroups.map((g) => (
          <MenuItem key={g} glyph="layers" label={g} onSelect={() => onMoveToGroup(g)} />
        ))}
        {/* The separator only divides New group… from rows above it. */}
        {conv.group || otherGroups.length > 0 ? <DropdownMenuSeparator className={MENU_SEPARATOR} /> : null}
        <MenuItem glyph="add-circle" label={t("sidebar.new_group")} onSelect={onNewGroup} />
      </Submenu>

      {/* Dragging the row onto a project header does the same thing. */}
      {projects && onMoveToProject && projects.length > 0 && (
        <Submenu glyph="folder-with-files" label={t("sidebar.move_to_project")}>
          {projects.map((project) => (
            <MenuItem key={project.id} glyph="folder" folder label={project.name} onSelect={() => onMoveToProject(project.id)} />
          ))}
        </Submenu>
      )}

      <DropdownMenuSeparator className={MENU_SEPARATOR} />

      <MenuItem glyph="link" label={t("sidebar.copy_link")} keys={{ key: "C" }} onSelect={onCopyLink} />
      <Submenu glyph="file-download" label={t("sidebar.export")}>
        <MenuItem glyph="file-text" label={t("sidebar.export_markdown")} onSelect={() => onExport("md")} />
        <MenuItem glyph="code-square" label={t("sidebar.export_html")} onSelect={() => onExport("html")} />
      </Submenu>

      <DropdownMenuSeparator className={MENU_SEPARATOR} />

      <MenuItem
        glyph={conv.archived ? "archive-up" : "archive"}
        label={conv.archived ? t("sidebar.unarchive") : t("sidebar.archive")}
        keys={{ key: "A" }}
        onSelect={onToggleArchive}
      />
      <MenuItem glyph="trash-bin-trash" label={t("sidebar.delete")} keys={{ key: "D" }} danger onSelect={onDelete} />
    </MenuContent>
  );
}

/** The menu of a row inside a multi-chat selection: what applies to all
 *  of them at once. */
export function BulkConvMenu({
  count,
  groups,
  projects,
  onMoveToProject,
  onMoveToGroup,
  onArchive,
  onClear,
}: {
  count: number;
  groups: string[];
  projects?: ConvMenuProject[];
  onMoveToProject?: (projectId: string) => void;
  onMoveToGroup: (group: string) => void;
  onArchive: () => void;
  onClear: () => void;
  onClose: () => void;
}) {
  const { t, text } = useTranslation();
  return (
    <MenuContent>
      <div className={GROUP_LABEL}>{text(`${count} chats selected`, `已选 ${count} 个聊天`)}</div>
      {projects && onMoveToProject && projects.length > 0 && (
        <Submenu glyph="folder-with-files" label={t("sidebar.move_to_project")}>
          {projects.map((project) => (
            <MenuItem key={project.id} glyph="folder" folder label={project.name} onSelect={() => onMoveToProject(project.id)} />
          ))}
        </Submenu>
      )}
      <Submenu glyph="layers" label={t("sidebar.move_to_group")}>
        <MenuItem glyph="close-circle" label={t("sidebar.remove_from_group")} onSelect={() => onMoveToGroup("")} />
        {groups.filter(Boolean).map((g) => (
          <MenuItem key={g} glyph="layers" label={g} onSelect={() => onMoveToGroup(g)} />
        ))}
      </Submenu>
      <DropdownMenuSeparator className={MENU_SEPARATOR} />
      <MenuItem glyph="archive" label={t("sidebar.archive")} onSelect={onArchive} />
      <DropdownMenuSeparator className={MENU_SEPARATOR} />
      <MenuItem glyph="close-circle" label={text("Clear selection", "取消选择")} onSelect={onClear} />
    </MenuContent>
  );
}

/* ---- item primitives ------------------------------------------- */

/** A row that opens its submenu to the right (hover, click or
 *  ArrowRight) — Nova's DropdownMenuSub with its trailing chevron. */
function Submenu({ glyph, label, children }: { glyph: MenuGlyph; label: string; children: React.ReactNode }) {
  return (
    <DropdownMenuSub>
      <DropdownMenuSubTrigger className={cn(itemCls(false), MENU_ITEM_STATES)}>
        <MenuRow glyph={glyph} label={label} submenu />
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent className={cn(MENU_PANEL, "text-text-primary")}>
        {children}
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  );
}

// Rows share the canonical `itemCls` from menu-styles and the shared row
// (MenuRow). Delete is the one danger row (red text and icon). Selecting
// a row closes the menu (Radix), which reaches the parent through
// onOpenChange.
function MenuItem({
  glyph,
  label,
  keys,
  danger,
  folder,
  onSelect,
}: {
  glyph: MenuGlyph;
  label: string;
  keys?: MenuKeys;
  danger?: boolean;
  /** A project row: its folder takes the folder colour. */
  folder?: boolean;
  onSelect: () => void;
}) {
  return (
    <DropdownMenuItem
      onSelect={onSelect}
      className={cn(
        itemCls(false, danger),
        MENU_ITEM_STATES,
        danger && "data-[highlighted]:bg-[color-mix(in_srgb,var(--accent-red)_15%,transparent)] data-[highlighted]:text-accent-red",
      )}
    >
      <MenuRow glyph={glyph} label={label} keys={keys} danger={danger} iconClassName={folder ? "text-[var(--folder-color)]" : undefined} />
    </DropdownMenuItem>
  );
}
