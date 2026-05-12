"use client";

/**
 * /settings — port of web/public/html/settings.html.
 *
 * Two-tab layout: "LLM Providers" + "General". Each tab is a separate
 * React section. Replaces the legacy switchSettingsSection /
 * _loadProvidersSettings / _loadGeneralSettings flow that wrote to
 * #settingsContent via innerHTML.
 */
import { useState } from "react";
import styles from "./settings-page.module.css";
import { GeneralSection } from "./general-section";
import { ProvidersSection } from "./providers-section";
import { SearchProvidersSection } from "./search-providers-section";

type Tab = "providers" | "search" | "general";

export function SettingsPage() {
  const [tab, setTab] = useState<Tab>("providers");
  return (
    <div className="main">
      <div className={styles.view}>
        <div className={styles.topbar}>
          <span className={styles.title}>Settings</span>
        </div>
        <div
          className={
            styles.body +
            (tab === "providers" || tab === "search"
              ? " " + styles.providersWide
              : "")
          }
        >
          <div className={styles.nav}>
            <div
              className={
                styles.navItem + (tab === "providers" ? " " + styles.active : "")
              }
              onClick={() => setTab("providers")}
            >
              LLM Providers
            </div>
            <div
              className={
                styles.navItem + (tab === "search" ? " " + styles.active : "")
              }
              onClick={() => setTab("search")}
            >
              Web Search
            </div>
            <div
              className={
                styles.navItem + (tab === "general" ? " " + styles.active : "")
              }
              onClick={() => setTab("general")}
            >
              General
            </div>
          </div>
          <div className={styles.content}>
            {tab === "providers" ? (
              <ProvidersSection />
            ) : tab === "search" ? (
              <SearchProvidersSection />
            ) : (
              <GeneralSection />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
