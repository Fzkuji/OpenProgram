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
import { useSessionStore } from "@/lib/session-store";
import { refreshFunctionsList } from "@/lib/programs-actions";
import { UserMenuFooter } from "../user-menu-footer";
import { SessionsList } from "./sessions-list";
import { FavoritesList } from "./favorites-list";
import {
  sidebarNavActionClass,
  sidebarNavIconClass,
  sidebarNavIconSvgClass,
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

  const [open, setOpen] = useState<boolean>(true);
  const [favCollapsed, setFavCollapsed] = useState(false);
  const [convCollapsed, setConvCollapsed] = useState(false);
  // Refresh-button states (matches legacy spin → checkmark → revert).
  const [refreshing, setRefreshing] = useState(false);
  const [refreshDone, setRefreshDone] = useState(false);
  const refreshSvgRef = useRef<SVGSVGElement>(null);

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
    programs: pathname.startsWith("/functions"),
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
        "[transition:width_0.3s_ease,min-width_0.3s_ease] " +
        (open
          ? "w-sidebar-w"
          : "w-[48px] min-w-[48px] collapsed")
      }
    >
      <div className="flex h-[48px] shrink-0 items-center justify-between p-[8px] box-border">
        <div
          className={
            "flex h-[32px] min-w-0 flex-1 items-center overflow-hidden " +
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
          title="Toggle sidebar"
          type="button"
        >
          <svg width="20" height="20" viewBox="0 0 256 256" fill="currentColor">
            <path d="M216,40H40A16,16,0,0,0,24,56V200a16,16,0,0,0,16,16H216a16,16,0,0,0,16-16V56A16,16,0,0,0,216,40Zm0,160H88V56H216V200Z" />
          </svg>
        </button>
      </div>

      <div className="flex flex-col gap-px shrink-0 px-[8px] pt-[8px]">
        <div
          className={sidebarNavItemClass}
          id="navNewChat"
          onClick={newChat}
          role="button"
        >
          <span
            className="flex size-[22.4px] shrink-0 -mx-[3.2px] items-center
              justify-center rounded-full bg-[rgba(151,149,140,0.15)]
              text-nav-color transition-colors duration-150 ease-out
              group-hover:bg-[rgba(151,149,140,0.25)]
              group-hover:[transform:rotate(-3deg)_scale(1.1)]
              group-active:bg-text-primary
              group-active:[transform:rotate(6deg)_scale(0.98)]
              [transition:transform_0.3s_cubic-bezier(0.165,0.85,0.45,1),background_0.15s_ease,color_0.15s_ease]
              group-hover:text-nav-color-hover"
          >
            <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor">
              <path d="M10.75 4.75a.75.75 0 0 0-1.5 0v4.5h-4.5a.75.75 0 0 0 0 1.5h4.5v4.5a.75.75 0 0 0 1.5 0v-4.5h4.5a.75.75 0 0 0 0-1.5h-4.5v-4.5Z" />
            </svg>
          </span>
          <span className={sidebarNavLabelClass}>New chat</span>
        </div>

        <Link
          href="/functions"
          className={
            sidebarNavItemClass +
            (navActive.programs ? " " + sidebarNavItemActiveClass : "")
          }
          id="navPrograms"
        >
          <span className={sidebarNavIconClass}>
            <svg className={sidebarNavIconSvgClass} viewBox="0 0 20 20" fill="currentColor">
              <path d="M4.25 2A2.25 2.25 0 0 0 2 4.25v2.5A2.25 2.25 0 0 0 4.25 9h2.5A2.25 2.25 0 0 0 9 6.75v-2.5A2.25 2.25 0 0 0 6.75 2h-2.5Zm0 9A2.25 2.25 0 0 0 2 13.25v2.5A2.25 2.25 0 0 0 4.25 18h2.5A2.25 2.25 0 0 0 9 15.75v-2.5A2.25 2.25 0 0 0 6.75 11h-2.5Zm9-9A2.25 2.25 0 0 0 11 4.25v2.5A2.25 2.25 0 0 0 13.25 9h2.5A2.25 2.25 0 0 0 18 6.75v-2.5A2.25 2.25 0 0 0 15.75 2h-2.5Zm0 9A2.25 2.25 0 0 0 11 13.25v2.5A2.25 2.25 0 0 0 13.25 18h2.5A2.25 2.25 0 0 0 18 15.75v-2.5A2.25 2.25 0 0 0 15.75 11h-2.5Z" />
            </svg>
          </span>
          <span className={sidebarNavLabelClass}>Functions</span>
          <span
            className={
              sidebarNavActionClass +
              " inline-flex size-[24px] items-center justify-center rounded-[6px]" +
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
            title="Refresh"
          >
            {refreshDone ? (
              <span>&#10003;</span>
            ) : (
              <svg
                ref={refreshSvgRef}
                width="12"
                height="12"
                viewBox="0 0 16 16"
                fill="currentColor"
                className={refreshing ? "animate-spin-refresh" : ""}
              >
                <path d="M8 1.5a6.5 6.5 0 1 0 6.5 6.5h-1.5A5 5 0 1 1 8 3V1.5z" />
                <path d="M8 0l3 3-3 3V0z" />
              </svg>
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
        >
          <span className={sidebarNavIconClass}>
            <svg className={sidebarNavIconSvgClass} viewBox="0 0 20 20" fill="currentColor">
              <path d="M10 1.5a1 1 0 0 1 .894.553l1.987 3.975 4.388.638a1 1 0 0 1 .554 1.706l-3.175 3.094.749 4.368a1 1 0 0 1-1.451 1.054L10 14.85l-3.946 2.038a1 1 0 0 1-1.451-1.054l.75-4.368L2.177 8.372a1 1 0 0 1 .554-1.706l4.388-.638L9.106 2.053A1 1 0 0 1 10 1.5Z" />
            </svg>
          </span>
          <span className={sidebarNavLabelClass}>Skills</span>
        </Link>

        <Link
          href="/plugins"
          className={
            sidebarNavItemClass +
            (navActive.plugins ? " " + sidebarNavItemActiveClass : "")
          }
          id="navPlugins"
        >
          <span className={sidebarNavIconClass}>
            <svg className={sidebarNavIconSvgClass} viewBox="0 0 20 20" fill="currentColor">
              <path d="M7 2a1 1 0 0 1 1 1v2h4V3a1 1 0 1 1 2 0v2h1.5A1.5 1.5 0 0 1 17 6.5V8h2a1 1 0 1 1 0 2h-2v4h2a1 1 0 1 1 0 2h-2v1.5a1.5 1.5 0 0 1-1.5 1.5H14v-2a1 1 0 1 0-2 0v2H8v-2a1 1 0 1 0-2 0v2H4.5A1.5 1.5 0 0 1 3 17.5V16H1a1 1 0 1 1 0-2h2v-4H1a1 1 0 1 1 0-2h2V6.5A1.5 1.5 0 0 1 4.5 5H6V3a1 1 0 0 1 1-1Z" />
            </svg>
          </span>
          <span className={sidebarNavLabelClass}>Plugins</span>
        </Link>

        <Link
          href="/mcp"
          className={
            sidebarNavItemClass +
            (navActive.mcp ? " " + sidebarNavItemActiveClass : "")
          }
          id="navMcp"
        >
          <span className={sidebarNavIconClass}>
            <svg className={sidebarNavIconSvgClass} viewBox="0 0 20 20" fill="currentColor">
              <path d="M3 4.5A2.5 2.5 0 0 1 5.5 2h9A2.5 2.5 0 0 1 17 4.5v3A2.5 2.5 0 0 1 14.5 10h-9A2.5 2.5 0 0 1 3 7.5v-3Zm2 0a.5.5 0 0 0 0 1h2a.5.5 0 0 0 0-1H5Zm0 8A2.5 2.5 0 0 0 2.5 15v.5A2.5 2.5 0 0 0 5 18h10a2.5 2.5 0 0 0 2.5-2.5V15A2.5 2.5 0 0 0 15 12.5H5Zm0 2h2a.5.5 0 0 1 0 1H5a.5.5 0 0 1 0-1Z" />
            </svg>
          </span>
          <span className={sidebarNavLabelClass}>MCP Servers</span>
        </Link>

        <Link
          href="/memory"
          className={
            sidebarNavItemClass +
            (navActive.memory ? " " + sidebarNavItemActiveClass : "")
          }
          id="navMemory"
        >
          <span className={sidebarNavIconClass}>
            <svg className={sidebarNavIconSvgClass} viewBox="0 0 20 20" fill="currentColor">
              <path d="M2 4.5A2.5 2.5 0 0 1 4.5 2h11a2.5 2.5 0 0 1 0 5h-11A2.5 2.5 0 0 1 2 4.5ZM2.75 9.083a.75.75 0 0 0 0 1.5h14.5a.75.75 0 0 0 0-1.5H2.75ZM2.75 12.663a.75.75 0 0 0 0 1.5h14.5a.75.75 0 0 0 0-1.5H2.75ZM2.75 16.25a.75.75 0 0 0 0 1.5h14.5a.75.75 0 1 0 0-1.5H2.75Z" />
            </svg>
          </span>
          <span className={sidebarNavLabelClass}>Memory</span>
        </Link>

        <Link
          href="/chats"
          className={
            sidebarNavItemClass +
            " sidebar-nav-chats" +
            (navActive.chats ? " " + sidebarNavItemActiveClass : "")
          }
          id="navChats"
        >
          <span className={sidebarNavIconClass}>
            <svg
              className={sidebarNavIconSvgClass}
              viewBox="0 0 20 20"
              fill="currentColor"
              aria-hidden="true"
            >
              <path
                className="nav-chats-bubble-l"
                d="M3.505 2.365A41.369 41.369 0 0 1 9 2c1.863 0 3.697.124 5.495.365 1.247.167 2.18 1.108 2.435 2.268a4.45 4.45 0 0 0-.577-.069 43.141 43.141 0 0 0-4.706 0C9.229 4.696 7.5 6.727 7.5 8.998v2.24c0 1.413.67 2.735 1.76 3.562l-2.98 2.98A.75.75 0 0 1 5 17.25v-3.443c-.501-.048-1-.106-1.495-.172C2.033 13.438 1 12.162 1 10.72V5.28c0-1.441 1.033-2.717 2.505-2.914Z"
              />
              <path
                className="nav-chats-bubble-r"
                d="M14 6c-.762 0-1.52.02-2.271.062C10.157 6.148 9 7.472 9 8.998v2.24c0 1.519 1.147 2.839 2.71 2.935.214.013.428.024.642.034.2.009.385.09.518.224l2.35 2.35a.75.75 0 0 0 1.28-.531v-2.07c1.453-.195 2.5-1.463 2.5-2.915V8.998c0-1.526-1.157-2.85-2.729-2.936A41.645 41.645 0 0 0 14 6Z"
              />
            </svg>
          </span>
          <span className={sidebarNavLabelClass}>Chats</span>
        </Link>
      </div>

      {/* Favorite functions — only when at least one favourite exists
          and the sidebar isn't collapsed. */}
      {open && hasFavorites && (
        <SidebarSection
          id="favSection"
          title="Favorite Functions"
          collapsed={favCollapsed}
          onToggle={() => setFavCollapsed((v) => !v)}
          className="shrink-0 pt-[16px]"
        >
          <div
            id="favList"
            className="flex flex-col gap-px max-h-[131px] overflow-y-auto
              overflow-x-hidden px-[8px] [scrollbar-width:none]
              [&::-webkit-scrollbar]:hidden"
          >
            <FavoritesList />
          </div>
        </SidebarSection>
      )}

      {open && (
        <SidebarSection
          id="convSection"
          title="Recents"
          collapsed={convCollapsed}
          onToggle={() => setConvCollapsed((v) => !v)}
          className="flex flex-1 min-h-0 flex-col pt-[16px]"
        >
          <div
            id="convList"
            className="flex flex-1 min-h-0 flex-col gap-px overflow-x-hidden
              overflow-y-auto px-[8px] [scrollbar-width:none]
              [&::-webkit-scrollbar]:hidden"
          >
            <SessionsList />
          </div>
        </SidebarSection>
      )}

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
 * Collapsible section in the sidebar (Favorite Programs / Recents).
 * Header is a click-target showing the title + a "Show/Hide" hint that
 * fades in on hover; body is rendered only when the section is open.
 * `className` is the outer-container layout (flex / shrink / padding)
 * that used to live on `.sidebar-favorites` / `.sidebar-conversations`
 * in 02-sidebar.css — pass it in instead.
 */
function SidebarSection({
  id,
  title,
  collapsed,
  onToggle,
  className,
  children,
}: {
  id: string;
  title: string;
  collapsed: boolean;
  onToggle: () => void;
  className: string;
  children: React.ReactNode;
}) {
  return (
    <div id={id} className={className}>
      <div
        className="group flex shrink-0 cursor-pointer select-none items-center
          px-[16px] py-[4px]"
        onClick={onToggle}
      >
        <span className="text-[12px] font-normal text-text-muted">{title}</span>
        <span
          className={
            "ml-auto text-[12px] text-text-muted " +
            "transition-opacity duration-150 ease-out " +
            (collapsed
              ? "opacity-75"
              : "opacity-0 group-hover:opacity-75")
          }
        >
          {collapsed ? "Show" : "Hide"}
        </span>
      </div>
      {!collapsed && children}
    </div>
  );
}
