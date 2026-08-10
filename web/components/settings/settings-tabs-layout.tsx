"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import styles from "./settings-page.module.css";
import { prefetchSettings } from "@/lib/prefs/settings-cache";
import { useTranslation } from "@/lib/i18n";

export type SettingsTab = "providers" | "usage" | "search" | "channels" | "general" | "system";

/**
 * Shell for the Settings tabs — topbar, sticky nav column, content
 * slot. Splits the previous SettingsPage's state-driven tab switching
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
  const { t } = useTranslation();
  // Mounted once when the user enters /settings/* (now an app-router
  // layout). useEffect fires once for the whole settings visit — no
  // remount per tab click — so the topbar + nav don't tear down + set
  // up between pages.
  useEffect(() => { prefetchSettings(); }, []);

  // Derive the active tab from the current URL instead of taking it
  // as a prop. Each page now only renders the section body; the
  // layout's nav highlights itself.
  const pathname = usePathname() || "";
  const active: SettingsTab = (() => {
    if (pathname.startsWith("/settings/usage")) return "usage";
    if (pathname.startsWith("/settings/search")) return "search";
    if (pathname.startsWith("/settings/channels")) return "channels";
    if (pathname.startsWith("/settings/general")) return "general";
    if (pathname.startsWith("/settings/system")) return "system";
    return "providers";
  })();

  const isWide =
    active === "providers" || active === "search" || active === "channels";
  return (
    <div className="main">
      <div className={styles.view}>
        <div className={styles.topbar}>
          <span className={styles.title}>{t("settings.title")}</span>
        </div>
        <div
          className={styles.body + (isWide ? " " + styles.providersWide : "")}
        >
          {/* Landmark + current-page marker: these are real <Link>s, so
              they already tab and activate; what was missing is a screen
              reader being able to find the tab column and hear which
              tab is open. */}
          <nav className={styles.nav} aria-label={t("settings.title")}>
            <Link
              href="/settings/providers"
              className={
                styles.navItem +
                (active === "providers" ? " " + styles.active : "")
              }
              aria-current={active === "providers" ? "page" : undefined}
            >
              {t("settings.tab.providers")}
            </Link>
            <Link
              href="/settings/usage"
              className={
                styles.navItem +
                (active === "usage" ? " " + styles.active : "")
              }
              aria-current={active === "usage" ? "page" : undefined}
            >
              {t("settings.tab.usage")}
            </Link>
            <Link
              href="/settings/search"
              className={
                styles.navItem +
                (active === "search" ? " " + styles.active : "")
              }
              aria-current={active === "search" ? "page" : undefined}
            >
              {t("settings.tab.search")}
            </Link>
            <Link
              href="/settings/channels"
              className={
                styles.navItem +
                (active === "channels" ? " " + styles.active : "")
              }
              aria-current={active === "channels" ? "page" : undefined}
            >
              {t("settings.tab.channels")}
            </Link>
            <Link
              href="/settings/general"
              className={
                styles.navItem +
                (active === "general" ? " " + styles.active : "")
              }
              aria-current={active === "general" ? "page" : undefined}
            >
              {t("settings.tab.general")}
            </Link>
            <Link
              href="/settings/system"
              className={
                styles.navItem +
                (active === "system" ? " " + styles.active : "")
              }
              aria-current={active === "system" ? "page" : undefined}
            >
              {t("settings.tab.system")}
            </Link>
          </nav>
          <div className={styles.content}>{children}</div>
        </div>
      </div>
    </div>
  );
}
