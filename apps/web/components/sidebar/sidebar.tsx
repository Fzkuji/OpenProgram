"use client";

/**
 * Left Sidebar — React port of apps/web/public/html/_sidebar.html +
 * apps/web/public/js/shared/sidebar.js.
 *
 * Re-uses the existing global CSS classes from
 *   apps/web/app/styles/02-sidebar.css   (sidebar / nav / fav / footer)
 *   apps/web/app/styles/03-settings.css  (conv-item)
 * so visual parity with the legacy template is exact. The CSS module
 * only carries a handful of React-only extras (empty-state hints +
 * clearAll row hover).
 *
 * Data sources (this slice):
 *   - the conversation list comes from `useSessionStore` (store.conversations,
 *     fed by the runtime-bridge via conv-store-mirror).
 *   - `useWindowGlobals` reads functions / meta from the functions store.
 *   - `useSessionStore` is still used for `openFnForm` plumbing
 *     (clickFunction is the global that calls it).
 *
 * This shell owns collapse state, new-chat behavior, favourites and
 * sessions. Fixed navigation and its refresh action live in
 * `sidebar-primary-nav.tsx`.
 */

import { memo, useCallback, useEffect, useLayoutEffect, useState, useRef } from "react";
import {
  type AnimatedNavIconHandle,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
  PlusIcon,
} from "../animated-icons";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import { useTranslation } from "@/lib/i18n";
import { activateOnKey } from "@/lib/utils";
import { UserMenuFooter } from "../user-menu-footer";
import { SessionsList } from "./sessions/sessions-list";
import { FavoritesList } from "./sessions/favorites-list";
import { SectionHeader } from "./sessions/section-header";
import {
  sidebarNavItemClass,
  sidebarNavLabelClass,
  sidebarToggleClass,
} from "./nav-classes";
import { SidebarPrimaryNav } from "./sidebar-primary-nav";
import { Sidebar as SidebarShell } from "@/components/ui/sidebar";
import { useWindowGlobals } from "./use-window-globals";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { openNewChat } from "@/lib/tabs/navigation/open-session";
import { useResizableRail } from "../layout/use-resizable-rail";
import { useStableScrollEnd } from "./use-stable-scroll-end";
import { useNavLinksReturn } from "./use-nav-links-return";

// At or below this width the expanded sidebar is a fixed overlay over the
// page (styles/base/layout.css), so it starts collapsed and closes after navigation.
const NARROW_QUERY = "(max-width: 900px)";

function isNarrowViewport(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.(NARROW_QUERY).matches === true;
}

function readPersistedSidebarOpen(): boolean {
  if (typeof window === "undefined") return true;
  // Narrow screens always start collapsed: expanded, the sidebar is an
  // overlay that would cover the chat. The stored choice still applies
  // once the window is wide again.
  if (isNarrowViewport()) return false;
  try {
    const stored = localStorage.getItem("sidebarOpen");
    if (stored === null) return true;
    return stored !== "0";
  } catch {
    return true;
  }
}

export const Sidebar = memo(function Sidebar() {
  const { t, text } = useTranslation();

  const [open, setOpen] = useState<boolean>(true);
  const {
    style: railStyle,
    shellProps: railShellProps,
    resizeHandleProps,
    closedSettled,
  } = useResizableRail({
    open,
    minWidth: 180,
    maxWidth: 480,
    defaultWidth: 288,
    direction: 1,
    storageKey: "openprogram.left-sidebar-width",
    maxWindowShare: 0.3,
  });
  const [favCollapsed, setFavCollapsed] = useState(false);
  // 下面滚动区一旦向下滚动就为 true —— 驱动固定 New chat 行底部的
  // 分隔线（Claude 风格）。
  const [navScrolled, setNavScrolled] = useState(false);
  const toggleIconRef = useRef<AnimatedNavIconHandle>(null);
  const toggleOpenIconRef = useRef<AnimatedNavIconHandle>(null);
  const newChatIconRef = useRef<AnimatedNavIconHandle>(null);
  const navScrollerRef = useRef<HTMLDivElement>(null);
  const navSpacerRef = useRef<HTMLDivElement>(null);
  const navLinksRef = useRef<HTMLDivElement>(null);
  useStableScrollEnd(navScrollerRef, navSpacerRef);
  useNavLinksReturn(open, closedSettled, navScrollerRef, navLinksRef);

  const { availableFunctions, programsMeta } = useWindowGlobals();
  const favSet = new Set(programsMeta.favorites || []);
  const hasFavorites = (availableFunctions || []).some((f) =>
    favSet.has(f.name),
  );

  // 挂载时从 localStorage 同步。必须在首次绘制前（useLayoutEffect）：
  // useEffect 在绘制后才跑，收起状态刷新页面会先画一帧展开、再播
  // 收起动画。右侧栏没这毛病，左侧栏也不该有。也回写共享状态
  // `runtimeState.sidebarOpen`。
  useLayoutEffect(() => {
    const persisted = readPersistedSidebarOpen();
    setOpen(persisted);
    runtimeState.sidebarOpen = persisted;
  }, []);
  // 水合前由 layout.tsx 内联脚本打上的强制收起属性，要等 DOM 上的
  // collapsed 类名和持久化状态一致后才摘。不能无条件摘：并发水合下
  // layout effect 里的 setOpen 不保证同步刷新，第一次 effect 跑的时
  // 候类名可能还是展开态——那时摘掉属性就会露一帧展开再播收起动画
  // （实测 t=311ms 正是这个洞）。
  useEffect(() => {
    const el = document.getElementById("sidebar");
    if (!el) return;
    const wantCollapsed = !readPersistedSidebarOpen();
    if (el.classList.contains("collapsed") === wantCollapsed) {
      document.documentElement.removeAttribute("data-sidebar-closed");
    }
  }, [open]);

  // On narrow screens the expanded sidebar overlays the page; dismiss it
  // once the user navigates. Not persisted, so the stored choice stands.
  const pathname = usePathname();
  const lastPathname = useRef(pathname);
  useEffect(() => {
    if (lastPathname.current === pathname) return;
    lastPathname.current = pathname;
    if (isNarrowViewport()) {
      setOpen(false);
      runtimeState.sidebarOpen = false;
    }
  }, [pathname]);

  // Crossing into the narrow breakpoint turns the expanded sidebar into an
  // overlay over the chat; collapse it then, and dismiss it on any press
  // outside. Neither is persisted, so widening again keeps the stored choice.
  useEffect(() => {
    const mq = window.matchMedia?.(NARROW_QUERY);
    if (!mq) return;
    const onChange = (event: MediaQueryListEvent) => {
      if (!event.matches) return;
      setOpen(false);
      runtimeState.sidebarOpen = false;
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!isNarrowViewport()) return;
      const target = event.target as Node | null;
      const el = document.getElementById("sidebar");
      if (!el || !target || el.contains(target)) return;
      // Menus and dialogs opened from the sidebar portal outside it.
      if ((target as Element).closest?.("[data-radix-popper-content-wrapper], [role='menu'], [role='dialog']")) return;
      setOpen(false);
      runtimeState.sidebarOpen = false;
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [open]);

  function toggleSidebar() {
    setOpen((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("sidebarOpen", next ? "1" : "0");
      } catch {
        /* ignore */
      }
      runtimeState.sidebarOpen = next;
      return next;
    });
  }

  const newChat = useCallback((options?: { fresh?: boolean }) => {
    const draftId = openNewChat(options);
    // Same route (/chat), so the pathname effect below does not fire;
    // dismiss the narrow-screen overlay here too.
    if (isNarrowViewport()) {
      setOpen(false);
      runtimeState.sidebarOpen = false;
    }
    return draftId;
  }, []);

  return (
    <SidebarShell
      id="sidebar"
      side="left"
      /* Landmark so screen readers can jump straight to navigation
         instead of tabbing in from the top of the document. */
      role="navigation"
      aria-label={text("Sidebar", "侧边栏")}
      // The shadcn floating shell (components/ui/sidebar.tsx) owns the
      // card: 7px gutter, rounded-2xl, raised shadow. This adds the
      // width transition and the stable hooks: `relative` anchors the
      // resize handle; `.sidebar` + `.collapsed` are what legacy
      // scrollbar.js, right-dock.css's `[data-view]` cascade and the
      // `.sidebar.collapsed` collapsed-state rules in styles/base/sidebar.css select.
      className={
        "sidebar relative shrink-0 rail-shell " + (open ? "" : "collapsed")
      }
      style={railStyle}
      {...railShellProps}
    >
      {/* The resize handle draws nothing and stays mounted in both
          states; closed or closing, it takes no pointer
          (use-resizable-rail.ts). */}
      <div
        {...resizeHandleProps}
        aria-label={text("Resize sidebar", "调整侧边栏宽度")}
      />
      <div className="rail-content">
      {/* The toggle rides the card's right edge, 8px in when open and
          centred in the collapsed card (styles/base/layout.css
          .rail-header); the wordmark stays at the left edge, clipped just
          before the toggle, and fades. */}
      <div className="rail-header">
        <div
          className="rail-open-only rail-wordmark"
        >
          <span className="text-fs-lg font-bold tracking-[-0.01em] whitespace-nowrap">
            <span style={{ color: "var(--text-bright)" }}>Open</span>
            <span
              style={{
                background: "linear-gradient(90deg, var(--wordmark-from) 0%, var(--wordmark-to) 100%)",
                WebkitBackgroundClip: "text",
                backgroundClip: "text",
                WebkitTextFillColor: "transparent",
              }}
            >
              Program
            </span>
          </span>
        </div>
        <button
          className={sidebarToggleClass}
          onClick={toggleSidebar}
          onMouseEnter={() => (open ? toggleIconRef : toggleOpenIconRef).current?.startAnimation?.()}
          onMouseLeave={() => (open ? toggleIconRef : toggleOpenIconRef).current?.stopAnimation?.()}
          aria-label={open ? text("Hide sidebar", "隐藏侧边栏") : text("Show sidebar", "显示侧边栏")}
          type="button"
        >
          {/* Both glyphs stay mounted and cross-fade with the rail's move
              (.rail-toggle-glyphs, styles/base/layout.css). */}
          <span className="rail-toggle-glyphs" aria-hidden="true">
            <PanelLeftCloseIcon ref={toggleIconRef} size={20} className="rail-toggle-close" />
            <PanelLeftOpenIcon ref={toggleOpenIconRef} size={20} className="rail-toggle-open" />
          </span>
        </button>
      </div>

      {/* 固定在顶部的 New chat 行（不随下面列表滚动）。默认它紧贴下面的
          Functions（底 padding 1px），只有向下滚动时才拉开 8px 空白 +
          由滚动区顶边的 inset 分隔线分组；滚回顶部又收回 1px。间隔是
          「滚动时后加的」，不常驻占位。 */}
      {/* New chat 块：默认与下面 Functions 只隔 1px（下面滚动区的 pt-px），
          自身无底 padding——不占任何额外空白。滚动时的空白+分隔线由一个
          绝对定位浮层盖上（见下方 navScrolled 浮层），与本块无关、不占
          布局、默认不存在。 */}
      <div className="flex flex-col gap-px shrink-0 px-[8px] pt-[8px]">
        <div
          className={sidebarNavItemClass + " rail-row"}
          id="navNewChat"
          onClick={() => newChat()}
          onMouseEnter={() => newChatIconRef.current?.startAnimation?.()}
          onMouseLeave={() => newChatIconRef.current?.stopAnimation?.()}
          role="button"
          tabIndex={0}
          onKeyDown={activateOnKey(newChat)}
        >
          <span
            className="flex size-[22.4px] shrink-0 -mx-[3.2px] items-center
              justify-center rounded-full bg-[color:var(--new-chat-chip)]
              text-nav-color transition-colors duration-150 ease-out
              group-hover:bg-[color:var(--new-chat-chip-hover)]
              group-hover:[transform:scale(1.1)]
              group-active:bg-text-primary
              group-active:[transform:scale(0.98)]
              [transition:transform_0.3s_cubic-bezier(0.165,0.85,0.45,1),background_0.15s_ease,color_0.15s_ease]
              group-hover:text-nav-color-hover"
          >
            <PlusIcon ref={newChatIconRef} size={16} />
          </span>
          <span className={sidebarNavLabelClass}>{t("nav.new_chat")}</span>
        </div>
      </div>

      {/* New chat 以下（导航链接 + 收藏 + 会话列表）一起滚动。滚动区外
          包一层 relative 容器，用来锚定「滚动时才出现的浮层」。 */}
      <div className="relative flex flex-1 min-h-0 flex-col">
        {/* 滚动时才出现的浮层：绝对定位贴在滚动区顶部，画一小段空白 +
          底部分隔线，盖在滚动内容之上。未滚动时不渲染。外层随侧栏一直
          挂着，收起时与只在展开态出现的内容一起淡出。 */}
        <div
          className="rail-open-only pointer-events-none absolute inset-x-0 top-0 z-10 h-[8px]"
          aria-hidden="true"
        >
          {navScrolled && (
            <div
              className="size-full bg-sidebar"
              style={{ boxShadow: "inset 0 -1px 0 0 var(--border)" }}
            />
          )}
        </div>
        {/* layoutScroll: project groups animate layout inside this
            scroller, so their positions are measured in its content, not
            shifted by its scrollTop. */}
        <motion.div
          ref={navScrollerRef}
          layoutScroll
          className="rail-scroll flex flex-1 min-h-0 flex-col overflow-y-auto overflow-x-hidden
          [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          onScroll={(e) => {
            const s = e.currentTarget.scrollTop > 0;
            setNavScrolled((prev) => (prev === s ? prev : s));
          }}
        >
          <div ref={navLinksRef} className="shrink-0">
            <SidebarPrimaryNav />
          </div>

          {/* Favorite functions — only when at least one favourite exists.
          Like the conversation list below it, it stays mounted while the
          sidebar is collapsed, at the open width, faded out. */}
          {hasFavorites && (
            <SidebarSection
              id="favSection"
              title={t("sidebar.favorite_functions")}
              collapsed={favCollapsed}
              onToggle={() => setFavCollapsed((v) => !v)}
              // px-[8px] on the SECTION (not just the list) so the header
              // label lands at the same 16px indent as the Recents section
              // headers below — those sit inside #convList's px-[8px] AND
              // their own SectionHeader px-[8px] (8+8). Without it the fav
              // header was 8px too far left of "Today" / "Yesterday".
              // No vertical padding: the SectionHeader's own top padding is
              // the one separator, so every label sits 16px below whatever
              // precedes it.
              className="rail-open-only rail-open-width px-[8px]"
            >
              <div id="favList" className="flex flex-col gap-px">
                <FavoritesList />
              </div>
            </SidebarSection>
          )}

          {/* The conversation list — classic Recents (date buckets by
              default; State / Flat via the filter's Group-by row), or the
              project folder tree when Group-by → Project. In project mode
              the group headers' ＋ buttons run the same newChat flow as
              the nav item above, then record the project for that draft
              key. Mounted in both states, at the open width: collapsed, it
              is faded out, and inert once the close has settled
              (use-resizable-rail.ts). */}
          <div
            id="convSection"
            className="rail-open-only rail-open-width flex flex-col"
          >
            {/* No gap: each section header's own top padding spaces the
                sections, so labels stay 16px below what precedes them. */}
            <div id="convList" className="flex flex-col px-[8px]">
              <SessionsList onNewChat={newChat} />
            </div>
          </div>
          {/* Stands in for removed rows at the end; see useStableScrollEnd. */}
          <div ref={navSpacerRef} className="shrink-0" aria-hidden="true" />
        </motion.div>
      </div>
      {/* /relative 浮层容器 */}

      </div>
      {/* User menu footer — rendered directly here (no portal), beside
         `.rail-content` rather than in it: it is laid out at the open width
         in both states (styles/base/sidebar.css), the avatar glides between
         its two resting places with the card, and the name and the chevron
         fade. */}
      <UserMenuFooter />
    </SidebarShell>
  );
});

/**
 * Collapsible section in the sidebar (currently just Favorite
 * Functions). The header is delegated to the shared `SectionHeader`, so
 * the label + collapse chevron match the Recents date/group buckets
 * exactly; the body is rendered only when the section is open.
 * `className` is the outer-container layout (flex / shrink / padding)
 * that used to live on `.sidebar-favorites` / `.sidebar-conversations`
 * in 02-sidebar.css — pass it in instead. The outer element also
 * carries `group/sec` so hovering the section reveals the chevron.
 */
function SidebarSection({
  id,
  title,
  collapsed,
  onToggle,
  className,
  headerActions,
  children,
}: {
  id: string;
  title: string;
  collapsed: boolean;
  onToggle: () => void;
  className: string;
  /** Optional controls rendered at the right of the header (e.g. the
   *  Recents filter button). Clicks inside are stopped from toggling
   *  the section. */
  headerActions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    // group/sec → hovering anywhere in the section (header or body)
    // reveals the collapse chevron, exactly like the Recents buckets.
    <div id={id} className={className + " group/sec"}>
      <SectionHeader
        name={title}
        collapsible
        collapsed={collapsed}
        onToggle={onToggle}
        actions={headerActions}
      />
      {!collapsed && children}
    </div>
  );
}
