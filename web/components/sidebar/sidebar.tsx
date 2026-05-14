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
 *   - `useLegacyGlobals` polls `window.conversations`,
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
import { useLegacyGlobals } from "./use-legacy-globals";

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

  const { availableFunctions, programsMeta } = useLegacyGlobals();
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
    programs: pathname.startsWith("/programs"),
    memory: pathname.startsWith("/memory"),
    chats: pathname.startsWith("/chats"),
  };

  return (
    <div id="sidebar" className={"sidebar" + (open ? "" : " collapsed")}>
      <div className="sidebar-header">
        <div className="logo">
          <img src="/images/logo.svg" alt="OpenProgram" className="logo-img" />
        </div>
        <button
          className="sidebar-toggle"
          onClick={toggleSidebar}
          title="Toggle sidebar"
          type="button"
        >
          <svg width="20" height="20" viewBox="0 0 256 256" fill="currentColor">
            <path d="M216,40H40A16,16,0,0,0,24,56V200a16,16,0,0,0,16,16H216a16,16,0,0,0,16-16V56A16,16,0,0,0,216,40Zm0,160H88V56H216V200Z" />
          </svg>
        </button>
      </div>

      <div className="sidebar-nav-group">
        <div
          className="sidebar-nav-item"
          id="navNewChat"
          onClick={newChat}
          role="button"
        >
          <span className="sidebar-nav-icon">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path d="M10.75 4.75a.75.75 0 0 0-1.5 0v4.5h-4.5a.75.75 0 0 0 0 1.5h4.5v4.5a.75.75 0 0 0 1.5 0v-4.5h4.5a.75.75 0 0 0 0-1.5h-4.5v-4.5Z" />
            </svg>
          </span>
          <span className="sidebar-nav-label">New chat</span>
        </div>

        <Link
          href="/programs"
          className={
            "sidebar-nav-item" + (navActive.programs ? " active" : "")
          }
          id="navPrograms"
        >
          <span className="sidebar-nav-icon">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path d="M4.25 2A2.25 2.25 0 0 0 2 4.25v2.5A2.25 2.25 0 0 0 4.25 9h2.5A2.25 2.25 0 0 0 9 6.75v-2.5A2.25 2.25 0 0 0 6.75 2h-2.5Zm0 9A2.25 2.25 0 0 0 2 13.25v2.5A2.25 2.25 0 0 0 4.25 18h2.5A2.25 2.25 0 0 0 9 15.75v-2.5A2.25 2.25 0 0 0 6.75 11h-2.5Zm9-9A2.25 2.25 0 0 0 11 4.25v2.5A2.25 2.25 0 0 0 13.25 9h2.5A2.25 2.25 0 0 0 18 6.75v-2.5A2.25 2.25 0 0 0 15.75 2h-2.5Zm0 9A2.25 2.25 0 0 0 11 13.25v2.5A2.25 2.25 0 0 0 13.25 18h2.5A2.25 2.25 0 0 0 18 15.75v-2.5A2.25 2.25 0 0 0 15.75 11h-2.5Z" />
            </svg>
          </span>
          <span className="sidebar-nav-label">Programs</span>
          <span
            className={
              "sidebar-nav-action refresh-btn" +
              (refreshing ? " spinning" : "") +
              (refreshDone ? " done" : "")
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
              >
                <path d="M8 1.5a6.5 6.5 0 1 0 6.5 6.5h-1.5A5 5 0 1 1 8 3V1.5z" />
                <path d="M8 0l3 3-3 3V0z" />
              </svg>
            )}
          </span>
        </Link>

        <Link
          href="/memory"
          className={"sidebar-nav-item" + (navActive.memory ? " active" : "")}
          id="navMemory"
        >
          <span className="sidebar-nav-icon">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path d="M2 4.5A2.5 2.5 0 0 1 4.5 2h11a2.5 2.5 0 0 1 0 5h-11A2.5 2.5 0 0 1 2 4.5ZM2.75 9.083a.75.75 0 0 0 0 1.5h14.5a.75.75 0 0 0 0-1.5H2.75ZM2.75 12.663a.75.75 0 0 0 0 1.5h14.5a.75.75 0 0 0 0-1.5H2.75ZM2.75 16.25a.75.75 0 0 0 0 1.5h14.5a.75.75 0 1 0 0-1.5H2.75Z" />
            </svg>
          </span>
          <span className="sidebar-nav-label">Memory</span>
        </Link>

        <Link
          href="/chats"
          className={
            "sidebar-nav-item sidebar-nav-chats group" +
            (navActive.chats ? " active" : "")
          }
          id="navChats"
        >
          <span className="sidebar-nav-icon">
            <svg
              width="20"
              height="20"
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
          <span className="sidebar-nav-label">Chats</span>
        </Link>
      </div>

      {/* Favorite programs — render the section only when at least
         one favourite exists, mirroring the legacy `empty` class. */}
      <div
        className={
          "sidebar-favorites" +
          (hasFavorites ? "" : " empty") +
          (favCollapsed ? " is-collapsed" : "")
        }
        id="favSection"
      >
        <div
          className="sidebar-section-header"
          onClick={() => setFavCollapsed((v) => !v)}
        >
          <span className="sidebar-section-title">Favorite Programs</span>
          <span className="sidebar-section-hint">
            {favCollapsed ? "Show" : "Hide"}
          </span>
        </div>
        {!favCollapsed && (
          <div className="sidebar-fav-list" id="favList">
            <FavoritesList />
          </div>
        )}
      </div>

      <div
        className={
          "sidebar-conversations" + (convCollapsed ? " is-collapsed" : "")
        }
        id="convSection"
      >
        <div
          className="sidebar-section-header"
          onClick={() => setConvCollapsed((v) => !v)}
        >
          <span className="sidebar-section-title">Recents</span>
          <span className="sidebar-section-hint">
            {convCollapsed ? "Show" : "Hide"}
          </span>
        </div>
        {!convCollapsed && (
          <div className="sidebar-conv-list" id="convList">
            <SessionsList />
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
