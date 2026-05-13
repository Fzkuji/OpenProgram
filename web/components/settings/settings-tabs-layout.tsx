"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import styles from "./settings-page.module.css";

export type SettingsTab = "providers" | "search" | "general";

/**
 * Shell for the three Settings tabs — topbar, sticky nav column,
 * content slot. Splits the previous SettingsPage's state-driven tab
 * switching into URL-routed subpages so refresh/back-button persist
 * the active tab.
 *
 * Each subpage at /settings/{providers,search,general} renders one
 * of these with the matching `active` prop and the section component
 * as `children`.
 */
export function SettingsTabsLayout({
  active,
  children,
}: {
  active: SettingsTab;
  children: ReactNode;
}) {
  const isWide = active === "providers" || active === "search";
  return (
    <div className="main">
      <div className={styles.view}>
        <div className={styles.topbar}>
          <span className={styles.title}>Settings</span>
        </div>
        <div
          className={styles.body + (isWide ? " " + styles.providersWide : "")}
        >
          <div className={styles.nav}>
            <Link
              href="/settings/providers"
              className={
                styles.navItem +
                (active === "providers" ? " " + styles.active : "")
              }
            >
              LLM Providers
            </Link>
            <Link
              href="/settings/search"
              className={
                styles.navItem +
                (active === "search" ? " " + styles.active : "")
              }
            >
              Web Search
            </Link>
            <Link
              href="/settings/general"
              className={
                styles.navItem +
                (active === "general" ? " " + styles.active : "")
              }
            >
              General
            </Link>
          </div>
          <div className={styles.content}>{children}</div>
        </div>
      </div>
    </div>
  );
}
