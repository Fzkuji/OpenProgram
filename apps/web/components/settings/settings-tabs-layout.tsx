"use client";

import { usePathname } from "next/navigation";
import {
  Bot,
  ChartNoAxesColumn,
  Brain,
  History,
  Import,
  Mic,
  MonitorCog,
  Palette,
  RadioTower,
  Server,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import styles from "./settings-page.module.css";
import { useTranslation } from "@/lib/i18n";
import { prefetchSystemPage } from "@/lib/settings/system-page-queries";
import { prefetchUsagePage } from "./token-usage";
import { ChromeIcon } from "../animated-icons";
import { ManagePageHeader } from "../ui/manage-page";
import { RailItem, RailSplit, RailToggle, SecondaryRail } from "../ui/secondary-rail";

export type SettingsTab = "providers" | "usage" | "search" | "environments" | "channels" | "browser" | "voice" | "memory" | "import" | "general" | "appearance" | "system" | "versions";

/** Keeps the pre-rail storage key, `settingsNavOpen`. */
const SETTINGS_RAIL = "settings";

/**
 * Shell for the Settings tabs — labeled nav column and content slot.
 * Splits the previous SettingsPage's state-driven tab switching
 * into URL-routed subpages so refresh/back-button persist the active
 * tab.
 *
 * Each subpage at /settings/{providers,search,environments,channels,import,general,appearance,…}
 * renders one of these with the matching `active` prop and the
 * section component as `children`.
 */
export function SettingsTabsLayout({
  children,
}: {
  children: ReactNode;
}) {
  const { t, text } = useTranslation();
  const queryClient = useQueryClient();

  useEffect(() => {
    prefetchUsagePage();
    return prefetchSystemPage(queryClient);
  }, [queryClient]);

  // Derive the active tab from the current URL instead of taking it
  // as a prop. Each page now only renders the section body; the
  // layout's nav highlights itself.
  const pathname = usePathname() || "";
  const active: SettingsTab = (() => {
    if (pathname.startsWith("/settings/usage")) return "usage";
    if (pathname.startsWith("/settings/search")) return "search";
    if (pathname.startsWith("/settings/environments")) return "environments";
    if (pathname.startsWith("/settings/channels")) return "channels";
    if (pathname.startsWith("/settings/browser")) return "browser";
    if (pathname.startsWith("/settings/voice")) return "voice";
    if (pathname.startsWith("/settings/memory")) return "memory";
    if (pathname.startsWith("/settings/import")) return "import";
    if (pathname.startsWith("/settings/general")) return "general";
    if (pathname.startsWith("/settings/appearance")) return "appearance";
    if (pathname.startsWith("/settings/system")) return "system";
    if (pathname.startsWith("/settings/versions")) return "versions";
    // Credential pools (/settings/auth) belong with LLM Providers.
    if (pathname.startsWith("/settings/auth")) return "providers";
    if (pathname.startsWith("/settings/providers")) return "providers";
    return "general";
  })();

  const tabs = [
    { id: "general" as const, href: "/settings/general", label: t("settings.tab.general"), Icon: SlidersHorizontal },
    { id: "appearance" as const, href: "/settings/appearance", label: t("settings.tab.appearance"), Icon: Palette },
    { id: "providers" as const, href: "/settings/providers", label: t("settings.tab.providers"), Icon: Bot },
    { id: "memory" as const, href: "/settings/memory", label: t("settings.tab.memory"), Icon: Brain },
    { id: "search" as const, href: "/settings/search", label: t("settings.tab.search"), Icon: Search },
    { id: "browser" as const, href: "/settings/browser", label: t("settings.tab.browser"), Icon: ChromeIcon },
    { id: "voice" as const, href: "/settings/voice", label: t("settings.tab.voice"), Icon: Mic },
    { id: "environments" as const, href: "/settings/environments", label: t("settings.tab.environments"), Icon: Server },
    { id: "channels" as const, href: "/settings/channels", label: t("settings.tab.channels"), Icon: RadioTower },
    { id: "import" as const, href: "/settings/import", label: t("settings.tab.import"), Icon: Import },
    { id: "usage" as const, href: "/settings/usage", label: t("settings.tab.usage"), Icon: ChartNoAxesColumn },
    { id: "system" as const, href: "/settings/system", label: t("settings.tab.system"), Icon: MonitorCog },
    { id: "versions" as const, href: "/settings/versions", label: t("settings.tab.versions"), Icon: History },
  ];
  return (
    <div className="main">
      <div className={styles.view}>
        <ManagePageHeader
          title={t("settings.title")}
          railToggle={(
            <RailToggle
              railKey={SETTINGS_RAIL}
              controls="settings-nav-items"
              label={{
                collapse: text("Collapse settings navigation", "收起设置导航"),
                expand: text("Expand settings navigation", "展开设置导航"),
              }}
            />
          )}
        />
        <RailSplit railKey={SETTINGS_RAIL}>
          <SecondaryRail railKey={SETTINGS_RAIL} id="settings-nav-items" label={t("settings.title")}>
            {tabs.map(({ id, href, label, Icon }) => (
              <RailItem key={id} href={href} icon={<Icon size={20} />} label={label} active={active === id} />
            ))}
          </SecondaryRail>
          <div className="p-[0] min-w-0 min-h-0 flex flex-col">{children}</div>
        </RailSplit>
      </div>
    </div>
  );
}
