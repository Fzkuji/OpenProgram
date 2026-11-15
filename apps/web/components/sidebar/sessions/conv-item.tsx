"use client";
/** One conversation row in the Recents list: title, status marker, inline
 *  rename, drag handles and its context / ⋯ menu. */

import { useEffect, useRef, useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { activateOnKey } from "@/lib/utils";
import { DropdownMenu, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { BulkConvMenu, ConvMenu } from "./conv-menu";
import { useSidebarMenu } from "../use-sidebar-menu";
import { SIDEBAR_ROW_ACTION_ICON } from "../nav-classes";
import styles from "../sidebar.module.css";
import type { ChatRowProps } from "./use-chat-drag";
import { type LegacyConv } from "./helpers";
import { StatusMarker } from "./status-marker";
import { ChannelLogo } from "@/components/channels/channel-logo";
import { channelBrand } from "@/lib/channels/channel-brands";

export function ConvItem({
  conv,
  label,
  active,
  selected,
  bulk,
  running,
  groups,
  onClick,
  onOpenInNewTab,
  onRename,
  onAutoRename,
  onTogglePin,
  onToggleArchive,
  onMoveToGroup,
  projects,
  onMoveToProject,
  dragProps,
  onCopyLink,
  onExport,
  onDelete,
}: {
  conv: LegacyConv;
  label: string;
  active: boolean;
  /** Part of a multi-chat selection. */
  selected?: boolean;
  /** Set while this row belongs to a selection of several chats: its menu acts on all of them. */
  bulk?: Omit<React.ComponentProps<typeof BulkConvMenu>, "groups" | "onClose">;
  running: boolean;
  groups: string[];
  onClick: (event?: React.MouseEvent<HTMLElement>) => void;
  /** Middle click: open the chat in a tab of its own. */
  onOpenInNewTab?: () => void;
  onRename: (title: string) => void;
  onAutoRename: () => void;
  onTogglePin: () => void;
  onToggleArchive: () => void;
  onMoveToGroup: (group: string) => void;
  /** The other projects the chat can move to, for its menu. */
  projects?: { id: string; name: string }[];
  onMoveToProject?: (projectId: string) => void;
  /** Pointer handlers that let the row be dragged onto a project. */
  dragProps?: ChatRowProps;
  onCopyLink: () => void;
  onExport: (format: "md" | "html") => void;
  onDelete: () => void;
}) {
  const { t, text } = useTranslation();
  // What a screen reader hears for the row: the logo is decorative, so a
  // channel chat names its app in words ("WeChat: title").
  const spoken = conv.channel ? `${channelBrand(conv.channel)}: ${label}` : label;
  const menu = useSidebarMenu();
  const menuOpen = menu.open;
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(label);
  const inputRef = useRef<HTMLInputElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);

  // `ui-list-item` (global) carries the row box — height, corner, padding,
  // gap-[12px] (which + the 16px marker slot aligns titles to the nav rows'
  // icon-slot), colour-transition, and the hover --bg-hover tint. Only the
  // chat-row extras stay here.
  // select-none: a press that becomes a drag onto a project must not
  // start selecting the title.
  // reflow / chatDragging: the row itself slides with the pointer when
  // carried onto another project, other rows ease aside to make room,
  // and all of them ease back if the drag is dropped.
  const base =
    "ui-list-item group relative shrink-0 overflow-hidden select-none" +
    " leading-[20px] whitespace-nowrap " + styles.reflow +
    (dragProps?.["data-dragging"] ? " " + styles.chatDragging : "");
  // Selected row: a background highlight marks it; the text steps down
  // from pure white to the warm off-white (--text-primary) so it isn't
  // glaringly bright.
  const colorCls = selected ? "bg-bg-selected text-text-primary"
    : active || menuOpen ? "bg-bg-hover text-text-primary" : "text-text-primary";
  // 右缘渐隐的三个状态（静止约 8px / 悬停 70%→92% / 滚动中）都在
  // styles/base/list-row.css 的 .title-fade 里，过渡也在那儿——渐变值本身不可插值，
  // 靠注册过的 --fade-a / --fade-b 百分比属性做动画。
  const maskHover = "title-fade";
  const maskScrolling = "title-fade title-fade-scrolling";

  // running → finishing edge animation (unchanged from before).
  const prevRunning = useRef(running);
  const [finishing, setFinishing] = useState(false);
  useEffect(() => {
    if (prevRunning.current && !running) {
      setFinishing(true);
      const t = setTimeout(() => setFinishing(false), 1200);
      prevRunning.current = running;
      return () => clearTimeout(t);
    }
    prevRunning.current = running;
  }, [running]);
  const stateCls = running ? "convRunning" : finishing ? "convFinishing" : "";

  // 长标题悬停跑马灯（claude.ai 同款）：悬停 1s 后把溢出部分匀速滚出来，
  // 移开立刻弹回。溢出量在 mouseenter 时实测，短标题 shift=0 不动。
  const labelOuterRef = useRef<HTMLSpanElement | null>(null);
  const labelInnerRef = useRef<HTMLSpanElement | null>(null);
  const marqueeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [marqueeShift, setMarqueeShift] = useState(0);
  // 悬停 1s 后开滚（延迟放 JS 定时器里，让左缘渐隐罩和滚动同帧切换）。
  function measureMarquee() {
    if (marqueeTimer.current) clearTimeout(marqueeTimer.current);
    marqueeTimer.current = setTimeout(() => {
      const outer = labelOuterRef.current;
      const inner = labelInnerRef.current;
      // A row being carried to another project keeps the pointer over
      // it the whole way; its title stays put.
      if (!outer || !inner || outer.closest("[data-dragging]")) return;
      const w = outer.clientWidth;
      const sw = inner.scrollWidth;
      // 右缘要给 ⋮ 按钮 + 渐隐留 36px：尾巴会撞进这个保留区的标题
      // 都滚，滚到尾巴正好停在保留区左侧（不与按钮重叠，也不钻进
      // 渐隐），不再多滚。
      const reserve = 36;
      const shift = sw - (w - reserve);
      if (shift > 4) setMarqueeShift(shift);
    }, 1000);
  }
  function stopMarquee() {
    if (marqueeTimer.current) clearTimeout(marqueeTimer.current);
    marqueeTimer.current = null;
    setMarqueeShift(0);
  }
  useEffect(() => stopMarquee, []);
  const carried = dragProps?.["data-dragging"] === true;
  useEffect(() => { if (carried) stopMarquee(); }, [carried]);

  function startRename() {
    setDraft(conv.title || label);
    setRenaming(true);
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
  }
  function commitRename() {
    setRenaming(false);
    const v = draft.trim();
    if (v && v !== (conv.title || "")) onRename(v);
  }

  function newGroup() {
    const name = window.prompt(t("sidebar.new_group_prompt"));
    if (name && name.trim()) onMoveToGroup(name.trim());
  }

  function openMenu(event: React.MouseEvent<HTMLElement>) {
    menu.show(event, [
      { id: "rename", label: t("sidebar.rename"), onSelect: startRename },
      { id: "auto-rename", label: t("sidebar.auto_rename"), onSelect: onAutoRename },
      { id: "pin", label: t(conv.pinned ? "sidebar.unpin" : "sidebar.pin"), onSelect: onTogglePin },
      { id: "group", label: t("sidebar.move_to_group"), children: [
        ...(conv.group ? [{ id: "ungroup", label: t("sidebar.remove_from_group"), onSelect: () => onMoveToGroup("") }] : []),
        ...groups.filter(group => group !== conv.group).map((group, index) => ({ id: `group:${index}`, label: group, onSelect: () => onMoveToGroup(group) })),
        { id: "new-group", label: t("sidebar.new_group"), onSelect: newGroup },
      ] },
      { id: "copy", label: t("sidebar.copy_link"), onSelect: onCopyLink },
      { id: "export", label: t("sidebar.export"), children: [
        { id: "export-md", label: t("sidebar.export_markdown"), onSelect: () => onExport("md") },
        { id: "export-html", label: t("sidebar.export_html"), onSelect: () => onExport("html") },
      ] },
      { id: "archive", label: t(conv.archived ? "sidebar.unarchive" : "sidebar.archive"), onSelect: onToggleArchive },
      { id: "delete", label: t("sidebar.delete"), separatorBefore: true, onSelect: onDelete },
    ]);
  }

  return (
    <DropdownMenu open={menuOpen} onOpenChange={menu.onOpenChange} modal={false}>
      <div
        ref={rowRef}
        className={`${base} ${colorCls} ${stateCls}`}
        /* The row is a div (the marquee/mask layering and the nested ⋮
           trigger rule out a real <button>), so it needs the button role
           plus explicit keyboard activation to be reachable at all.
           While renaming, the inner <input> owns the keyboard — drop the
           role and the tab stop so Tab/Enter go to the text field. */
        role={renaming ? undefined : "button"}
        tabIndex={renaming ? undefined : 0}
        aria-current={active ? "true" : undefined}
        data-selected={selected ? "true" : undefined}
        onKeyDown={renaming ? undefined : event => { if (event.target === event.currentTarget) activateOnKey(onClick)(event); }}
        {...(renaming ? {} : dragProps)}
        onClick={renaming ? undefined : (event) => {
          // A release that ended a drag is not a click on the chat.
          dragProps?.onClick?.(event);
          if (!event.defaultPrevented) onClick(event);
        }}
        // A middle press would start autoscroll; its click opens a new tab.
        onMouseDown={renaming || !onOpenInNewTab ? dragProps?.onMouseDown : (event) => {
          if (event.button === 1) event.preventDefault();
          dragProps?.onMouseDown?.(event);
        }}
        onAuxClick={renaming || !onOpenInNewTab ? undefined : (event) => {
          if (event.button !== 1) return;
          event.preventDefault();
          onOpenInNewTab();
        }}
        onMouseEnter={renaming ? undefined : measureMarquee}
        onMouseLeave={stopMarquee}
        onContextMenu={openMenu}
        /* 不给 title：悬停时标题自己滚动（measureMarquee 起的 marquee），
           原生 tooltip 会浮在相邻行上盖住列表，两者只留滚动这一种。 */
      >
        {/* Leading status marker (Claude-Code-style). Priority:
            needs_input → live running task → unread (red when the turn
            failed) → pinned → idle.
            A working row shows the arc even when pinned; waiting
            for input retains the amber marker. Until the
            server sends them, rows simply show working or idle.
            The marker's 16px-wide slot is the SAME width as the
            nav rows' icon slot (sidebarNavIconClass) — so with the row's
            12px gap the conversation title lines up at the exact same
            left indent as New chat / Functions / … above. */}
          <StatusMarker
            pinned={!!conv.pinned}
            state={
              // needs_input outranks working: a session waiting on the user
              // shows amber even while its run is technically still active.
              conv.status === "needs_input"
                ? "needs_input"
                : running
                  ? "working"
                  : conv.unread
                    ? conv.status === "failed" ? "failed" : "unread"
                    : "idle"
            }
            labels={{
              pinned: t("sidebar.pinned"),
              working: t("sidebar.running"),
              needsInput: t("sidebar.needs_input"),
              failed: t("sidebar.failed"),
              unread: t("sidebar.unread"),
            }}
          />

        {renaming ? (
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); commitRename(); }
              else if (e.key === "Escape") { e.preventDefault(); setRenaming(false); }
            }}
            className="flex-1 min-w-0 rounded-md border border-accent-orange
              bg-bg-input px-[6px] py-[2px] text-fs-base leading-[18px]
              text-text-bright outline-none"
          />
        ) : (
          // A channel chat's brand logo stands where the title starts and
          // the title follows 6px later; the logo stays put while a long
          // title scrolls.
          <span className="flex min-w-0 flex-1 items-center gap-[6px]">
            {conv.channel ? <span className="sr-only">{channelBrand(conv.channel)}: </span> : null}
            {conv.channel ? <ChannelLogo channel={conv.channel} size={14} /> : null}
            <span
              ref={labelOuterRef}
              className={`min-w-0 flex-1 overflow-hidden whitespace-nowrap text-fs-base leading-[20px] ${marqueeShift > 0 ? maskScrolling : maskHover}`}
            >
              <span
                ref={labelInnerRef}
                className="inline-block whitespace-nowrap"
                style={
                  marqueeShift > 0
                    ? {
                        transform: `translateX(-${marqueeShift}px)`,
                        // 速度约 40px/s（延迟已在定时器里）。
                        transition: `transform ${Math.max(600, marqueeShift * 25)}ms linear`,
                      }
                    : { transform: "translateX(0)", transition: "transform 200ms ease" }
                }
              >
                {label}
              </span>
            </span>
          </span>
        )}

        {/* ⋯ button — hover-visible; anchors the menu. */}
        {/* The menu opens at the click point: a zero-size trigger placed
            there inside the row, so the menu follows the row on scroll. */}
        <DropdownMenuTrigger asChild>
          <span
            aria-hidden="true"
            tabIndex={-1}
            className="pointer-events-none absolute size-0"
            // Read only for an open menu: a layout read in every row's render
            // forces a full layout each time the list renders.
            style={menuOpen ? menuAnchorStyle(menu.point, rowRef.current) : undefined}
          />
        </DropdownMenuTrigger>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              if (menuOpen) menu.close(); else openMenu(e);
            }}
            /* Was "Filter & sort" — that's the Recents header's button,
               not this row's. This one opens the conversation menu. */
            aria-label={text(`Options for ${spoken}`, `${spoken} 的操作`)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            className="absolute right-[4px] top-1/2 flex size-(--ui-button-h-xs) -translate-y-1/2
              items-center justify-center rounded-sm text-text-muted
              opacity-0 pointer-events-none transition-opacity duration-150 ease-out
              group-hover:opacity-100 group-hover:pointer-events-auto
              data-[state=open]:opacity-100 data-[state=open]:pointer-events-auto
              data-[state=open]:bg-bg-selected data-[state=open]:text-text-bright
              hover:bg-bg-selected hover:text-text-bright"
            data-state={menuOpen ? "open" : "closed"}
          >
            {/* The same ⋯ at the same size as the project row's. */}
            <MoreHorizontal size={SIDEBAR_ROW_ACTION_ICON} aria-hidden="true" />
          </button>
      </div>

        {bulk ? <BulkConvMenu {...bulk} groups={groups} onClose={menu.close} /> : <ConvMenu
          conv={conv}
          groups={groups}
          onRename={startRename}
          onAutoRename={onAutoRename}
          onTogglePin={onTogglePin}
          onToggleArchive={onToggleArchive}
          onMoveToGroup={onMoveToGroup}
          onNewGroup={newGroup}
          projects={projects}
          onMoveToProject={onMoveToProject}
          onCopyLink={onCopyLink}
          onExport={onExport}
          onDelete={onDelete}
          onClose={menu.close}
        />}
    </DropdownMenu>
  );
}

/** Where a row's context-menu anchor goes: the click point, relative to
 *  the row it sits in. */
function menuAnchorStyle(point: { x: number; y: number }, row: HTMLElement | null): React.CSSProperties {
  const rect = row?.getBoundingClientRect();
  return rect ? { left: point.x - rect.left, top: point.y - rect.top } : { left: 0, top: 0 };
}
