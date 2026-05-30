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
import { useUserProfile } from "@/lib/user-profile";
import { useTranslation } from "@/lib/i18n";
import { Avatar } from "@/components/avatar";
import {
  type AnimatedNavIconHandle,
  ChevronsUpDownIcon,
  CircleHelpIcon,
  SettingsIcon,
} from "@/components/animated-icons";
import styles from "./user-menu-footer.module.css";

export function UserMenuFooter() {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const { t } = useTranslation();
  // The footer is the local account chip → show the User profile
  // (name + avatar from /settings/general → User), not the agent's.
  const profile = useUserProfile();
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const settingsIconRef = useRef<AnimatedNavIconHandle>(null);
  const aboutIconRef = useRef<AnimatedNavIconHandle>(null);
  const chevronIconRef = useRef<AnimatedNavIconHandle>(null);
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
        onMouseEnter={() => chevronIconRef.current?.startAnimation?.()}
        onMouseLeave={() => chevronIconRef.current?.stopAnimation?.()}
      >
        {/* Avatar — DiceBear-generated SVG by default, seeded by the
            profile name so the same user always gets the same glyph.
            Pass ``profile.avatar`` through directly: when undefined
            (existing users with no avatar config) the Avatar
            component defaults to DiceBear ``shapes``, which is the
            visual upgrade. The outer CSS-module ``avatar`` class
            still owns sizing / ring / hover state. */}
        <Avatar
          className={`${styles.avatar} user-menu-footer-avatar`}
          size={36}
          name={profile.name}
          config={profile.avatar}
        />
        <span className={`${styles.info} user-menu-footer-info`}>
          <span className={styles.name}>{profile.name}</span>
          <span className={styles.subtitle}>{t("user.local_instance")}</span>
        </span>
        <ChevronsUpDownIcon
          ref={chevronIconRef}
          size={14}
          className={styles.chevron}
          aria-hidden="true"
        />
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
            <button
              type="button"
              className={styles.item}
              onClick={goSettings}
              onMouseEnter={() => settingsIconRef.current?.startAnimation?.()}
              onMouseLeave={() => settingsIconRef.current?.stopAnimation?.()}
            >
              <SettingsIcon ref={settingsIconRef} size={18} />
              {t("user.settings")}
            </button>
            <div className={styles.sep} />
            <a
              className={styles.item}
              href="https://github.com/Fzkuji/Agentic-Programming"
              target="_blank"
              rel="noopener"
              onClick={() => setOpen(false)}
              onMouseEnter={() => aboutIconRef.current?.startAnimation?.()}
              onMouseLeave={() => aboutIconRef.current?.stopAnimation?.()}
            >
              <CircleHelpIcon ref={aboutIconRef} size={18} />
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
