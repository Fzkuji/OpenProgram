"use client";

/**
 * User-menu footer for the legacy sidebar.
 *
 * Replaces the inline-onclick `.sidebar-footer` + `#userMenu` block
 * that lived in `web/public/html/_sidebar.html`. Mounted by AppShell
 * via a portal into the legacy sidebar's container so the chat-page
 * layout (which still uses _sidebar.html) gets a working footer
 * without touching the rest of that legacy markup.
 *
 * No legacy globals (`toggleUserMenu` / `openSettings`) are used —
 * everything is local React state + Next.js router.
 */

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { useAgentProfile } from "@/lib/agent-style";
import { useTranslation } from "@/lib/i18n";
import styles from "./user-menu-footer.module.css";

export function UserMenuFooter() {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const { t } = useTranslation();
  const profile = useAgentProfile();
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  // Viewport coords for the floating menu — recomputed when opening.
  // Used in collapsed sidebar so the menu can escape the sidebar's
  // ``overflow: hidden`` ancestor via a portal + ``position: fixed``.
  const [menuPos, setMenuPos] = useState<{
    left: number; bottom: number;
  } | null>(null);

  // True when the menu lives inside a collapsed sidebar — only then
  // we portal+fixed it out. Open sidebar keeps the original inline
  // popover anchored to the trigger's footer container.
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    if (!open) return;
    const trig = triggerRef.current;
    const sidebar = trig?.closest(".sidebar");
    const isCollapsed = !!sidebar && sidebar.classList.contains("collapsed");
    setCollapsed(isCollapsed);
    if (trig && isCollapsed) {
      const r = trig.getBoundingClientRect();
      // EXACT same viewport position as the open-sidebar menu would
      // have: open sidebar is 288px (--sidebar-width), the menu sits
      // inside it with left:8 right:8 → viewport coords [8, 280].
      // Reproduce those coords here (left=8, width=272), and anchor
      // the menu's bottom 8px above the trigger so it sits exactly
      // where the open variant did.
      setMenuPos({
        left: 8,
        bottom: window.innerHeight - r.top + 8,
      });
    } else {
      setMenuPos(null);
    }
    function onDocClick(e: MouseEvent) {
      const tgt = e.target as Node;
      if (ref.current && ref.current.contains(tgt)) return;
      if (menuRef.current && menuRef.current.contains(tgt)) return;
      setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    // Defer one tick so the click that opened us doesn't immediately close us.
    const t = setTimeout(() => {
      document.addEventListener("click", onDocClick);
      document.addEventListener("keydown", onKey);
    }, 0);
    return () => {
      clearTimeout(t);
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function goSettings() {
    setOpen(false);
    router.push("/settings");
  }

  return (
    <div className={`${styles.footer} user-menu-footer`} ref={ref}>
      <button
        ref={triggerRef}
        type="button"
        className={`${styles.trigger} user-menu-footer-trigger`}
        onClick={() => setOpen((v) => !v)}
      >
        <span
          className={`${styles.avatar} user-menu-footer-avatar`}
          style={{ background: profile.color, color: "#fff" }}
        >
          {profile.initial}
        </span>
        <span className={`${styles.info} user-menu-footer-info`}>
          <span className={styles.name}>{profile.name}</span>
          <span className={styles.subtitle}>{t("user.local_instance")}</span>
        </span>
      </button>
      {open && (() => {
        const menuBody = (
          <div
            ref={menuRef}
            className={styles.menu}
            role="menu"
            style={collapsed && menuPos ? {
              position: "fixed",
              left: `${menuPos.left}px`,
              bottom: `${menuPos.bottom}px`,
              right: "auto",
              // 288px sidebar - 8px left - 8px right padding = 272.
              // Match the open variant's visible width exactly.
              width: "272px",
            } : undefined}
          >
            <button type="button" className={styles.item} onClick={goSettings}>
              <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path d="M10.549 2C11.35 2 12 2.65 12 3.451c0 .195.138.403.385.501q.102.041.204.085l.09.032c.212.06.415.007.536-.114a1.453 1.453 0 0 1 2.055.001l.774.774.1.11a1.454 1.454 0 0 1-.1 1.945c-.138.138-.187.382-.081.625l.085.205.042.087c.108.192.289.298.459.298C17.35 8 18 8.65 18 9.451v1.098C18 11.35 17.35 12 16.549 12c-.17 0-.35.106-.46.298l-.041.087-.085.204c-.106.243-.057.488.08.626a1.453 1.453 0 0 1 0 2.055l-.773.774a1.453 1.453 0 0 1-2.055 0 .55.55 0 0 0-.535-.114l-.091.033q-.1.044-.203.084c-.247.098-.386.306-.386.5C12 17.35 11.35 18 10.548 18H9.452C8.65 18 8 17.35 8 16.548a.55.55 0 0 0-.298-.46l-.087-.041-.205-.085c-.243-.106-.487-.056-.625.082a1.453 1.453 0 0 1-1.944.1l-.11-.1-.775-.774a1.453 1.453 0 0 1 0-2.055l.047-.057a.56.56 0 0 0 .066-.478l-.032-.091-.085-.204c-.098-.247-.306-.385-.5-.385C2.65 12 2 11.35 2 10.549V9.45C2 8.65 2.65 8 3.451 8c.195 0 .402-.138.5-.385l.086-.205.032-.09a.56.56 0 0 0-.066-.48l-.048-.055a1.453 1.453 0 0 1 0-2.055l.775-.775.11-.1a1.453 1.453 0 0 1 1.945.1c.138.138.382.188.625.082q.102-.045.205-.086c.247-.098.385-.305.385-.5C8 2.65 8.65 2 9.451 2zM10 7a3 3 0 1 1 0 6 3 3 0 0 1 0-6m0 1a2 2 0 1 0 0 4 2 2 0 0 0 0-4" />
              </svg>
              {t("user.settings")}
            </button>
            <div className={styles.sep} />
            <a
              className={styles.item}
              href="https://github.com/Fzkuji/Agentic-Programming"
              target="_blank"
              rel="noopener"
              onClick={() => setOpen(false)}
            >
              <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path d="M10 2.5a7.5 7.5 0 1 1 0 15 7.5 7.5 0 0 1 0-15m0 1a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13m.1 5.51a.5.5 0 0 1 .4.49v3h1a.5.5 0 0 1 0 1h-3a.5.5 0 0 1 0-1h1V10h-1a.5.5 0 0 1 0-1H10zM10 6.5A.75.75 0 1 1 10 8a.75.75 0 0 1 0-1.5" />
              </svg>
              {t("user.about")}
            </a>
          </div>
        );
        // Collapsed sidebar clips the menu via overflow:hidden — portal
        // it to document.body and use fixed coords so it escapes.
        // Open sidebar uses the original in-place absolute popover.
        return collapsed && typeof document !== "undefined"
          ? createPortal(menuBody, document.body)
          : menuBody;
      })()}
    </div>
  );
}
