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
import { useCenterTabs } from "@/lib/state/center-tabs-store";
import { useTranslation } from "@/lib/i18n";
import { activateOnKey } from "@/lib/utils";
import { UserMenuFooter } from "../user-menu-footer";
import { SessionsList } from "./sessions-list";
import { FavoritesList } from "./favorites-list";
import { SectionHeader } from "./section-header";
import {
  sidebarNavItemClass,
  sidebarNavLabelClass,
  sidebarToggleClass,
} from "./nav-classes";
import { SidebarPrimaryNav } from "./sidebar-primary-nav";
import { useWindowGlobals } from "./use-window-globals";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { newSession } from "@/lib/runtime-bridge/conversations";
import { useResizableRail } from "../layout/use-resizable-rail";

function readPersistedSidebarOpen(): boolean {
  if (typeof window === "undefined") return true;
  try {
    return localStorage.getItem("sidebarOpen") !== "0";
  } catch {
    return true;
  }
}

export const Sidebar = memo(function Sidebar() {
  const { t, text } = useTranslation();

  const [open, setOpen] = useState<boolean>(true);
  const { style: railStyle, resizeHandleProps } = useResizableRail({
    open,
    minWidth: 180,
    maxWidth: 480,
    defaultWidth: 288,
    direction: 1,
  });
  const [favCollapsed, setFavCollapsed] = useState(false);
  // 下面滚动区一旦向下滚动就为 true —— 驱动固定 New chat 行底部的
  // 分隔线（Claude 风格）。
  const [navScrolled, setNavScrolled] = useState(false);
  const toggleIconRef = useRef<AnimatedNavIconHandle>(null);
  const newChatIconRef = useRef<AnimatedNavIconHandle>(null);

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

  const newChat = useCallback(() => {
    // Focus-or-create（浏览器地址栏语义）：已经开着的"新会话"界面
    // ——未发消息的草稿会话 tab 或 NTP tab——直接跳过去，而不是每点
    // 一次就多冒一个 tab。真想多开几个空会话用 tab 条上的 ＋。
    const s = useCenterTabs.getState();
    const existingDraft = s.tabs.find(
      (t) => t.kind === "session" && t.draft && t.sessionId,
    );
    const existingNtp = existingDraft
      ? null
      : s.tabs.find((t) => t.kind === "ntp");
    let draftId: string;
    if (existingDraft) {
      if (s.activeId !== existingDraft.id) s.setActive(existingDraft.id);
      draftId = existingDraft.sessionId!;
    } else if (existingNtp) {
      // NTP 是"新标签页"界面：占住它并转成草稿会话，语义同在 NTP 里
      // 点 New session。
      if (s.activeId !== existingNtp.id) s.setActive(existingNtp.id);
      draftId = useCenterTabs.getState().claimDraftSessionTab();
    } else {
      draftId = s.openDraftSessionTab();
    }
    newSession(draftId);
    return draftId;
  }, []);

  return (
    <div
      id="sidebar"
      /* Landmark so screen readers can jump straight to navigation
         instead of tabbing in from the top of the document. */
      role="navigation"
      aria-label={text("Sidebar", "侧边栏")}
      className={
        // Shell layout — bg / border / flex column / width transition.
        // `relative` is the anchor for the (legacy) `#userMenuFooterMount`
        // portal target; we keep it even though UserMenuFooter is now
        // rendered directly here. `.sidebar` + `.collapsed` classes are
        // retained as hooks for: legacy scrollbar.js (selects .sidebar),
        // right-dock.css's `[data-view]` cascade, and the small
        // `.sidebar.collapsed *` overflow/scrollbar-width override + the
        // `.sidebar-nav-item/-label/-action/.user-menu-footer-info`
        // collapsed-state rules in 02-sidebar.css.
        "sidebar relative flex shrink-0 flex-col overflow-hidden " +
        "bg-bg-secondary border-r border-[var(--border)] " +
        (open ? "" : "collapsed")
      }
      style={railStyle}
    >
      {open && (
        <div
          {...resizeHandleProps}
          className="absolute inset-y-0 right-0 z-10 w-[6px] cursor-ew-resize"
          title={text("Resize sidebar", "调整侧边栏宽度")}
        />
      )}
      {/* 收起态（49px 窄轨）只剩收起按钮：改用 justify-center 并去掉左右
          padding，让唯一按钮在窄轨里水平居中，否则 justify-between + p-[8px]
          会把它推到右边（靠右 bug）。 */}
      <div
        className={
          "flex h-[48px] shrink-0 items-center box-border " +
          (open ? "justify-between p-[8px]" : "justify-center px-0 py-[8px]")
        }
      >
        <div
          className={
            "flex h-[var(--ui-list-h)] min-w-0 flex-1 items-center overflow-hidden " +
            "[transition:opacity_0.15s_ease,padding-left_0.3s_ease] " +
            (open ? "opacity-100 pl-[8px]" : "hidden opacity-0 pl-0")
          }
        >
          <span className="text-[20px] font-bold tracking-[-0.01em] whitespace-nowrap">
            <span style={{ color: "var(--text-bright)" }}>Open</span>
            <span
              style={{
                background: "linear-gradient(90deg, #3B9EFF 0%, #A855F7 100%)",
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
          onMouseEnter={() => toggleIconRef.current?.startAnimation?.()}
          onMouseLeave={() => toggleIconRef.current?.stopAnimation?.()}
          title={t("sidebar.toggle")}
          type="button"
        >
          {open ? (
            <PanelLeftCloseIcon ref={toggleIconRef} size={20} />
          ) : (
            <PanelLeftOpenIcon ref={toggleIconRef} size={20} />
          )}
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
          className={sidebarNavItemClass}
          id="navNewChat"
          onClick={newChat}
          onMouseEnter={() => newChatIconRef.current?.startAnimation?.()}
          onMouseLeave={() => newChatIconRef.current?.stopAnimation?.()}
          role="button"
          tabIndex={0}
          onKeyDown={activateOnKey(newChat)}
        >
          <span
            className="flex size-[22.4px] shrink-0 -mx-[3.2px] items-center
              justify-center rounded-full bg-[rgba(151,149,140,0.15)]
              text-nav-color transition-colors duration-150 ease-out
              group-hover:bg-[rgba(151,149,140,0.25)]
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
          底部分隔线，盖在滚动内容之上。默认（未滚动）display:none —— 完全
          不存在、不占布局、不与任何组件产生间距。滚回顶部即消失。 */}
        {navScrolled && (
          <div
            className="pointer-events-none absolute inset-x-0 top-0 z-10 h-[8px]
            bg-[var(--bg-sidebar,var(--bg-secondary))]"
            style={{ boxShadow: "inset 0 -1px 0 0 var(--border)" }}
            aria-hidden="true"
          />
        )}
        <div
          className="flex flex-1 min-h-0 flex-col overflow-y-auto overflow-x-hidden
          [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          onScroll={(e) => {
            const s = e.currentTarget.scrollTop > 0;
            setNavScrolled((prev) => (prev === s ? prev : s));
          }}
        >
          <SidebarPrimaryNav />

          {/* Favorite functions — only when at least one favourite exists
          and the sidebar isn't collapsed. */}
          {open && hasFavorites && (
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
              // No pt here: the SectionHeader's own pt-[15px] is the single
              // group separator. An extra pt-[16px] used to STACK on it, so
              // major groups (Favorites / Recents) sat ~16px lower than the
              // date sub-buckets — visibly inconsistent + too much empty
              // space above the section.
              // pb-[5px]: a small extra gap BELOW Favorites so the function
              // area reads as a distinct block from the conversation groups
              // that follow (only present when there are favourites).
              className="px-[8px] pb-[5px]"
            >
              <div id="favList" className="flex flex-col gap-px">
                <FavoritesList />
              </div>
            </SidebarSection>
          )}

          {open && (
            // The conversation list — classic Recents (date buckets by
            // default; State / Flat via the filter's Group-by row), or the
            // project folder tree when Group-by → Project. In project mode
            // the group headers' ＋ buttons run the same newChat flow as
            // the nav item above, then record the project for that draft key.
            <div id="convSection" className="flex flex-col">
              <div id="convList" className="flex flex-col gap-px px-[8px]">
                <SessionsList onNewChat={newChat} />
              </div>
            </div>
          )}
        </div>
      </div>
      {/* /relative 浮层容器 */}

      {/* User menu footer — rendered directly here (no portal). The
         AppShell's `#userMenuFooterMount` portal logic is now a no-op
         for left-sidebar pages (no element with that id exists) but
         is kept around for any code path that might still inject the
         legacy `_sidebar.html` template. */}
      <UserMenuFooter />
    </div>
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
