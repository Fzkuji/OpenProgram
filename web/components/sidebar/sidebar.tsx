"use client";

/**
 * Left Sidebar — React port of web/public/html/_sidebar.html +
 * web/public/js/shared/sidebar.js.
 *
 * Re-uses the existing global CSS classes from
 *   web/app/styles/02-sidebar.css   (sidebar / nav / fav / footer)
 *   web/app/styles/03-settings.css  (conv-item)
 * so visual parity with the legacy template is exact. The CSS module
 * only carries a handful of React-only extras (empty-state hints +
 * clearAll row hover).
 *
 * Data sources (this slice):
 *   - `useWindowGlobals` polls `window.conversations`,
 *     `window.availableFunctions`, `window.programsMeta` written by
 *     legacy `init.js` / WS handlers. When WSProvider gets wired in,
 *     swap to a `useSessionStore` subscription.
 *   - `useSessionStore` is still used for `openFnForm` plumbing
 *     (clickFunction is the global that calls it).
 *
 * Behaviours:
 *   - New chat   → router.push('/chat') + clear active session.
 *   - Programs   → /programs
 *   - Memory     → /memory
 *   - Chats      → /chats
 *   - Click conv → /s/<id>
 *   - Fav click  → legacy `clickFunction()` (opens fn form via store).
 *   - Refresh    → legacy `refreshFunctions()` (re-fetch + re-render).
 *   - Collapse   → CSS class toggle on `.sidebar`, persisted to
 *                  localStorage as `sidebarOpen`. We also write
 *                  `window.sidebarOpen` so legacy code that still
 *                  reads it stays in sync.
 */

import { useEffect, useState, useRef } from "react";
import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
// Sidebar icons. The six nav glyphs (functions / skills / plugins / mcp /
// memory / chats) AND the collapse toggle use the ANIMATED line set
// (pqoqubbw/icons — Lucide + Framer Motion, ./animated-nav-icons), each
// driven from its row's / button's hover. The toggle swaps by state:
// panel-left-close (chevron ‹) when open, panel-left-open (›) when
// collapsed. The other two chrome actions keep their own existing motion
// and just use the lucide LINE glyph: refresh = RefreshCw (spins on
// click). New-chat uses the animated PlusIcon (spins 180° on row hover);
// its circular badge still scales/brightens.
import { RefreshCw } from "lucide-react";
import {
  type AnimatedNavIconHandle,
  BoxesIcon,
  BrainIcon,
  GraduationCapIcon,
  LayersIcon,
  MessageCircleIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
  PlusIcon,
  WorkflowIcon,
} from "../animated-icons";
import { useSessionStore } from "@/lib/session-store";
import { refreshFunctionsList } from "@/lib/functions-actions";
import { useTranslation } from "@/lib/i18n";
import { UserMenuFooter } from "../user-menu-footer";
import { SessionsList } from "./sessions-list";
import { FavoritesList } from "./favorites-list";
import { SectionHeader } from "./section-header";
import {
  sidebarNavActionClass,
  sidebarNavIconClass,
  sidebarNavItemActiveClass,
  sidebarNavItemClass,
  sidebarNavLabelClass,
  sidebarToggleClass,
} from "./nav-classes";
import { useWindowGlobals } from "./use-window-globals";

function readPersistedSidebarOpen(): boolean {
  if (typeof window === "undefined") return true;
  try {
    return localStorage.getItem("sidebarOpen") !== "0";
  } catch {
    return true;
  }
}

export function Sidebar() {
  const router = useRouter();
  const pathname = usePathname();
  const setCurrentConv = useSessionStore((s) => s.setCurrentConv);
  const { t } = useTranslation();

  const [open, setOpen] = useState<boolean>(true);
  const [favCollapsed, setFavCollapsed] = useState(false);
  // Refresh-button states (matches legacy spin → checkmark → revert).
  const [refreshing, setRefreshing] = useState(false);
  const [refreshDone, setRefreshDone] = useState(false);
  const refreshSvgRef = useRef<SVGSVGElement>(null);
  // Animated nav icons (pqoqubbw/icons) are driven from the row's hover
  // — each Link's onMouseEnter/Leave calls the icon handle, so the whole
  // row is the hover target (claude.ai-style). Pilot: functions / skills
  // / mcp only.
  const functionsIconRef = useRef<AnimatedNavIconHandle>(null);
  const skillsIconRef = useRef<AnimatedNavIconHandle>(null);
  const mcpIconRef = useRef<AnimatedNavIconHandle>(null);
  const pluginsIconRef = useRef<AnimatedNavIconHandle>(null);
  const memoryIconRef = useRef<AnimatedNavIconHandle>(null);
  const chatsIconRef = useRef<AnimatedNavIconHandle>(null);
  const toggleIconRef = useRef<AnimatedNavIconHandle>(null);
  const newChatIconRef = useRef<AnimatedNavIconHandle>(null);

  const { availableFunctions, programsMeta } = useWindowGlobals();
  const favSet = new Set(programsMeta.favorites || []);
  const hasFavorites =
    (availableFunctions || []).some((f) => favSet.has(f.name));

  // On mount, sync from localStorage. Also publish to the legacy
  // global so any code still reading `window.sidebarOpen` agrees.
  useEffect(() => {
    const persisted = readPersistedSidebarOpen();
    setOpen(persisted);
    (window as unknown as { sidebarOpen?: boolean }).sidebarOpen = persisted;
  }, []);

  function toggleSidebar() {
    setOpen((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("sidebarOpen", next ? "1" : "0");
      } catch {
        /* ignore */
      }
      (window as unknown as { sidebarOpen?: boolean }).sidebarOpen = next;
      return next;
    });
  }

  // Auto-refresh the function catalogue: poll every 30s + refetch
  // whenever the tab regains focus. Drops new external harnesses
  // (symlinks added under openprogram/functions/agentics/) into the
  // sidebar without the user having to hit the refresh button.
  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      if (!cancelled && document.visibilityState === "visible") {
        void refreshFunctionsList();
      }
    };
    const id = window.setInterval(tick, 30_000);
    const onVis = () => {
      if (document.visibilityState === "visible") void refreshFunctionsList();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, []);

  // Expose the toggle as a window global so the legacy TopBar
  // hamburger button (and any other legacy caller) keeps working
  // after the migration. `window.restoreSidebarState` is a no-op
  // now — the React sidebar restores from localStorage in its own
  // mount effect — but we install a stub so any straggler calls
  // don't crash.
  useEffect(() => {
    const w = window as unknown as {
      toggleSidebar?: () => void;
      restoreSidebarState?: () => void;
    };
    const prevToggle = w.toggleSidebar;
    const prevRestore = w.restoreSidebarState;
    w.toggleSidebar = toggleSidebar;
    w.restoreSidebarState = () => {
      /* no-op: state is restored by the mount useEffect above */
    };
    return () => {
      w.toggleSidebar = prevToggle;
      w.restoreSidebarState = prevRestore;
    };
  }, []);

  function newChat() {
    setCurrentConv(null);
    if (pathname !== "/chat") {
      router.push("/chat");
      return;
    }
    // Already on /chat — fall through to the legacy reset so the
    // chat-area welcome screen / tree state / message list all clear.
    const w = window as unknown as { newSession?: () => void };
    if (typeof w.newSession === "function") w.newSession();
  }

  function doRefresh() {
    if (refreshing) return;
    setRefreshing(true);
    // Re-fetch /api/functions via the React-side helper; it mirrors
    // the result into both the zustand store and the legacy
    // `window.availableFunctions` global so React + legacy consumers
    // stay in sync.
    void refreshFunctionsList();
    // Mirror legacy spin → tick → revert timing.
    const svg = refreshSvgRef.current;
    if (svg) {
      const handler = () => {
        svg.removeEventListener("animationend", handler);
        setRefreshing(false);
        setRefreshDone(true);
        setTimeout(() => setRefreshDone(false), 800);
      };
      svg.addEventListener("animationend", handler);
      // Safety net: animation may not fire if user dropped the tab; reset after 1.2s.
      setTimeout(() => {
        if (refreshing) setRefreshing(false);
      }, 1200);
    } else {
      setTimeout(() => {
        setRefreshing(false);
        setRefreshDone(true);
        setTimeout(() => setRefreshDone(false), 800);
      }, 600);
    }
  }

  // Sync `.active` highlighting on nav items based on the route — purely
  // visual; the AppShell click-interceptor handles the actual routing.
  const navActive = {
    mcp: pathname.startsWith("/mcp"),
    functions: pathname.startsWith("/functions"),
    memory: pathname.startsWith("/memory"),
    chats: pathname.startsWith("/chats"),
    skills: pathname.startsWith("/skills"),
    plugins: pathname.startsWith("/plugins") || pathname.startsWith("/plugin/"),
  };

  return (
    <div
      id="sidebar"
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
        // 150ms is the sweet spot — visible enough to feel intentional
        // (so the icons don't pop), short enough not to drag. Same
        // cubic-bezier Claude uses on its info-opacity fade for a
        // consistent timing feel.
        "[transition:width_0.15s_cubic-bezier(0.165,0.84,0.44,1),min-width_0.15s_cubic-bezier(0.165,0.84,0.44,1)] " +
        (open
          ? "w-sidebar-w"
          : "w-[49px] min-w-[49px] collapsed")
      }
    >
      <div className="flex h-[48px] shrink-0 items-center justify-between p-[8px] box-border">
        <div
          className={
            "flex h-[var(--ui-list-h)] min-w-0 flex-1 items-center overflow-hidden " +
            "[transition:opacity_0.15s_ease,padding-left_0.3s_ease] " +
            (open ? "opacity-100 pl-[8px]" : "opacity-0 pl-0")
          }
        >
          <img
            src="/images/logo.svg"
            alt="OpenProgram"
            className="block h-[32px] w-auto"
          />
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

      <div className="flex flex-col gap-px shrink-0 px-[8px] pt-[8px]">
        <div
          className={sidebarNavItemClass}
          id="navNewChat"
          onClick={newChat}
          onMouseEnter={() => newChatIconRef.current?.startAnimation?.()}
          onMouseLeave={() => newChatIconRef.current?.stopAnimation?.()}
          role="button"
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

      {/* Everything below New chat scrolls together (Claude-style): the
          nav links + favourites + conversations are one scroll area, so a
          short window never hides the bottom. Only New chat (above) and the
          footer (below) stay pinned. */}
      <div
        className="flex flex-1 min-h-0 flex-col overflow-y-auto overflow-x-hidden
          [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        <div className="flex flex-col gap-px shrink-0 px-[8px] pt-[8px]">
        <Link
          href="/functions"
          className={
            sidebarNavItemClass +
            (navActive.functions ? " " + sidebarNavItemActiveClass : "")
          }
          id="navPrograms"
          onMouseEnter={() => functionsIconRef.current?.startAnimation?.()}
          onMouseLeave={() => functionsIconRef.current?.stopAnimation?.()}
        >
          <span className={sidebarNavIconClass}>
            <WorkflowIcon ref={functionsIconRef} size={20} />
          </span>
          <span className={sidebarNavLabelClass}>{t("nav.functions")}</span>
          <span
            className={
              sidebarNavActionClass +
              " inline-flex size-[22px] items-center justify-center rounded-[5px]" +
              " [transition:background_0.15s,color_0.15s,opacity_0.15s]" +
              " hover:bg-bg-hover hover:text-text-bright hover:!opacity-100" +
              " active:bg-bg-tertiary" +
              (refreshing || refreshDone ? " !opacity-100" : "") +
              (refreshDone ? " !text-[#4ade80]" : "")
            }
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              doRefresh();
            }}
            title={t("sidebar.refresh")}
          >
            {refreshDone ? (
              <span>&#10003;</span>
            ) : (
              <RefreshCw
                ref={refreshSvgRef}
                size={16}
                strokeWidth={2}
                className={refreshing ? "animate-spin-refresh" : ""}
              />
            )}
          </span>
        </Link>

        <Link
          href="/skills"
          className={
            sidebarNavItemClass +
            (navActive.skills ? " " + sidebarNavItemActiveClass : "")
          }
          id="navSkills"
          onMouseEnter={() => skillsIconRef.current?.startAnimation?.()}
          onMouseLeave={() => skillsIconRef.current?.stopAnimation?.()}
        >
          <span className={sidebarNavIconClass}>
            <GraduationCapIcon ref={skillsIconRef} size={20} />
          </span>
          <span className={sidebarNavLabelClass}>{t("nav.skills")}</span>
        </Link>

        <Link
          href="/plugins"
          className={
            sidebarNavItemClass +
            (navActive.plugins ? " " + sidebarNavItemActiveClass : "")
          }
          id="navPlugins"
          onMouseEnter={() => pluginsIconRef.current?.startAnimation?.()}
          onMouseLeave={() => pluginsIconRef.current?.stopAnimation?.()}
        >
          <span className={sidebarNavIconClass}>
            <BoxesIcon ref={pluginsIconRef} size={20} />
          </span>
          <span className={sidebarNavLabelClass}>{t("nav.plugins")}</span>
        </Link>

        <Link
          href="/mcp"
          className={
            sidebarNavItemClass +
            (navActive.mcp ? " " + sidebarNavItemActiveClass : "")
          }
          id="navMcp"
          onMouseEnter={() => mcpIconRef.current?.startAnimation?.()}
          onMouseLeave={() => mcpIconRef.current?.stopAnimation?.()}
        >
          <span className={sidebarNavIconClass}>
            <LayersIcon ref={mcpIconRef} size={20} />
          </span>
          <span className={sidebarNavLabelClass}>{t("nav.mcp")}</span>
        </Link>

        <Link
          href="/memory"
          className={
            sidebarNavItemClass +
            (navActive.memory ? " " + sidebarNavItemActiveClass : "")
          }
          id="navMemory"
          onMouseEnter={() => memoryIconRef.current?.startAnimation?.()}
          onMouseLeave={() => memoryIconRef.current?.stopAnimation?.()}
        >
          <span className={sidebarNavIconClass}>
            <BrainIcon ref={memoryIconRef} size={20} />
          </span>
          <span className={sidebarNavLabelClass}>{t("nav.memory")}</span>
        </Link>

        <Link
          href="/chats"
          className={
            sidebarNavItemClass +
            " sidebar-nav-chats" +
            (navActive.chats ? " " + sidebarNavItemActiveClass : "")
          }
          id="navChats"
          onMouseEnter={() => chatsIconRef.current?.startAnimation?.()}
          onMouseLeave={() => chatsIconRef.current?.stopAnimation?.()}
        >
          <span className={sidebarNavIconClass}>
            <MessageCircleIcon ref={chatsIconRef} size={20} aria-hidden="true" />
          </span>
          <span className={sidebarNavLabelClass}>{t("nav.chats")}</span>
        </Link>
      </div>

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
        // No "Recents" wrapper / collapse — the conversation list is a
        // SINGLE-LEVEL grouped list (date buckets / Working-Completed /
        // project), so the section headers ARE the top level. The
        // filter button lives on the first section header inside
        // SessionsList, right-aligned (Claude's layout).
        <div id="convSection" className="flex flex-col">
          <div id="convList" className="flex flex-col gap-px px-[8px]">
            <SessionsList />
          </div>
        </div>
      )}
      </div>

      {/* User menu footer — rendered directly here (no portal). The
         AppShell's `#userMenuFooterMount` portal logic is now a no-op
         for left-sidebar pages (no element with that id exists) but
         is kept around for any code path that might still inject the
         legacy `_sidebar.html` template. */}
      <UserMenuFooter />
    </div>
  );
}

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
