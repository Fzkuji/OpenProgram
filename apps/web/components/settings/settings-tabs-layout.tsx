"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Bot,
  ChartNoAxesColumn,
  Brain,
  MonitorCog,
  RadioTower,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import { useLayoutEffect, useState, type ReactNode } from "react";
import styles from "./settings-page.module.css";
import { useTranslation } from "@/lib/i18n";
import { ChromeIcon, PanelLeftCloseIcon, PanelLeftOpenIcon } from "../animated-icons";
import { sidebarToggleClass } from "../sidebar/nav-classes";

export type SettingsTab = "providers" | "usage" | "search" | "channels" | "browser" | "memory" | "general" | "system";

function readSettingsNavOpen(): boolean {
  if (typeof window === "undefined") return true;
  try {
    return localStorage.getItem("settingsNavOpen") !== "0";
  } catch {
    return true;
  }
}

/**
 * Shell for the Settings tabs — labeled nav column and content slot.
 * Splits the previous SettingsPage's state-driven tab switching
 * into URL-routed subpages so refresh/back-button persist the active
 * tab.
 *
 * Each subpage at /settings/{providers,search,channels,general}
 * renders one of these with the matching `active` prop and the
 * section component as `children`.
 */
export function SettingsTabsLayout({
  children,
}: {
  children: ReactNode;
}) {
  const { t, text } = useTranslation();
  const [navOpen, setNavOpen] = useState(true);
  useLayoutEffect(() => { setNavOpen(readSettingsNavOpen()); }, []);

  function toggleNav() {
    setNavOpen((current) => {
      const next = !current;
      try { localStorage.setItem("settingsNavOpen", next ? "1" : "0"); } catch { /* ignore */ }
      return next;
    });
  }

  // Derive the active tab from the current URL instead of taking it
  // as a prop. Each page now only renders the section body; the
  // layout's nav highlights itself.
  const pathname = usePathname() || "";
  const active: SettingsTab = (() => {
    if (pathname.startsWith("/settings/usage")) return "usage";
    if (pathname.startsWith("/settings/search")) return "search";
    if (pathname.startsWith("/settings/channels")) return "channels";
    if (pathname.startsWith("/settings/browser")) return "browser";
    if (pathname.startsWith("/settings/memory")) return "memory";
    if (pathname.startsWith("/settings/general")) return "general";
    if (pathname.startsWith("/settings/system")) return "system";
    if (pathname.startsWith("/settings/providers")) return "providers";
    return "general";
  })();

  const isWide =
    active === "providers" || active === "search" || active === "channels";
  const tabs = [
    { id: "general" as const, href: "/settings/general", label: t("settings.tab.general"), Icon: SlidersHorizontal },
    { id: "providers" as const, href: "/settings/providers", label: t("settings.tab.providers"), Icon: Bot },
    { id: "memory" as const, href: "/settings/memory", label: t("settings.tab.memory"), Icon: Brain },
    { id: "search" as const, href: "/settings/search", label: t("settings.tab.search"), Icon: Search },
    { id: "browser" as const, href: "/settings/browser", label: text("Browser", "浏览器"), Icon: ChromeIcon },
    { id: "channels" as const, href: "/settings/channels", label: t("settings.tab.channels"), Icon: RadioTower },
    { id: "usage" as const, href: "/settings/usage", label: t("settings.tab.usage"), Icon: ChartNoAxesColumn },
    { id: "system" as const, href: "/settings/system", label: t("settings.tab.system"), Icon: MonitorCog },
  ];
  return (
    <div className="main">
      <div className={styles.view}>
        <div
          className={
            styles.body +
            (isWide ? " " + styles.providersWide : "") +
            (!navOpen ? " " + styles.settingsNavCollapsed : "")
          }
        >
          {/* Landmark + current-page marker: these are real <Link>s, so
              they already tab and activate; what was missing is a screen
              reader being able to find the tab column and hear which
              tab is open. */}
          <nav className={styles.nav} aria-label={t("settings.title")}>
            <div className={styles.railHeader}>
              <span className={styles.railTitle}>{t("settings.title")}</span>
              <button
                type="button"
                className={sidebarToggleClass}
                onClick={toggleNav}
                aria-expanded={navOpen}
                aria-controls="settings-nav-items"
                aria-label={text(navOpen ? "Collapse settings navigation" : "Expand settings navigation", navOpen ? "收起设置导航" : "展开设置导航")}
                title={text(navOpen ? "Collapse settings navigation" : "Expand settings navigation", navOpen ? "收起设置导航" : "展开设置导航")}
              >
                {navOpen ? <PanelLeftCloseIcon size={20} /> : <PanelLeftOpenIcon size={20} />}
              </button>
            </div>
            <div id="settings-nav-items" className={styles.railItems}>
              {tabs.map(({ id, href, label, Icon }) => (
                <Link
                  key={id}
                  href={href}
                  className={styles.navItem + (active === id ? " " + styles.active : "")}
                  aria-current={active === id ? "page" : undefined}
                  aria-label={label}
                  title={!navOpen ? label : undefined}
                >
                  <span className={styles.railItemIcon}><Icon size={20} /></span>
                  <span className={styles.railItemLabel}>{label}</span>
                </Link>
              ))}
            </div>
          </nav>
          <div className={styles.content}>{children}</div>
        </div>
      </div>
    </div>
  );
}
