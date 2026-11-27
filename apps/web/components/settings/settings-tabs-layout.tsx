"use client";

import { usePathname } from "next/navigation";
import {
  Blocks,
  Bot,
  ChartNoAxesColumn,
  Brain,
  History,
  Import,
  Mic,
  Palette,
  RadioTower,
  Server,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import { Fragment, useEffect, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import styles from "./settings-page.module.css";
import { useTranslation } from "@/lib/i18n";
import { ensureMachines } from "@/lib/machines/store";
import { prefetchSettingsPages } from "@/lib/settings/settings-urls";
import { prefetchSystemPage } from "@/lib/settings/system-page-queries";
import { versionsQuery } from "@/lib/settings/versions";
import { ChromeIcon } from "../animated-icons";
import { ManagePageHeader } from "../ui/manage-page";
import { RailGroupLabel, RailItem, RailSplit, RailToggle, SecondaryRail } from "../ui/secondary-rail";

export type SettingsTab = "providers" | "usage" | "harnesses" | "search" | "machines" | "channels" | "browser" | "voice" | "memory" | "import" | "general" | "appearance" | "versions";

/** Keeps the pre-rail storage key, `settingsNavOpen`. */
const SETTINGS_RAIL = "settings";

/**
 * Shell for the Settings tabs — labeled nav column and content slot.
 * Splits the previous SettingsPage's state-driven tab switching
 * into URL-routed subpages so refresh/back-button persist the active
 * tab.
 *
 * Each subpage at /settings/{providers,search,machines,channels,import,general,appearance,…}
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

  // Every tab's data is read as Settings opens (the open tab's own reads
  // start first: a child's effects run before this one), so clicking a tab
  // finds its answer already kept and the page appears filled.
  useEffect(() => {
    prefetchSettingsPages();
    prefetchSystemPage(queryClient);
    void queryClient.prefetchQuery(versionsQuery);
    void ensureMachines().catch(() => {});
  }, [queryClient]);

  // Derive the active tab from the current URL instead of taking it
  // as a prop. Each page now only renders the section body; the
  // layout's nav highlights itself.
  const pathname = usePathname() || "";
  const active: SettingsTab = (() => {
    if (pathname.startsWith("/settings/usage")) return "usage";
    if (pathname.startsWith("/settings/harnesses")) return "harnesses";
    if (pathname.startsWith("/settings/search")) return "search";
    if (pathname.startsWith("/settings/machines")) return "machines";
    if (pathname.startsWith("/settings/channels")) return "channels";
    if (pathname.startsWith("/settings/browser")) return "browser";
    if (pathname.startsWith("/settings/voice")) return "voice";
    if (pathname.startsWith("/settings/memory")) return "memory";
    if (pathname.startsWith("/settings/import")) return "import";
    if (pathname.startsWith("/settings/general")) return "general";
    if (pathname.startsWith("/settings/appearance")) return "appearance";
    if (pathname.startsWith("/settings/versions")) return "versions";
    if (pathname.startsWith("/settings/providers")) return "providers";
    return "general";
  })();

  // Grouped by what the reader is looking for: themselves, the models and
  // what they can do, the computers and connections they reach, and the data
  // kept. A collapsed rail hides the group labels and keeps the icons.
  const groups = [
    {
      label: text("Personal", "个人"),
      tabs: [
        { id: "general" as const, href: "/settings/general", label: t("settings.tab.general"), Icon: SlidersHorizontal },
        { id: "appearance" as const, href: "/settings/appearance", label: t("settings.tab.appearance"), Icon: Palette },
      ],
    },
    {
      label: text("Models and abilities", "模型与能力"),
      tabs: [
        { id: "providers" as const, href: "/settings/providers", label: t("settings.tab.providers"), Icon: Bot },
        { id: "usage" as const, href: "/settings/usage", label: t("settings.tab.usage"), Icon: ChartNoAxesColumn },
        { id: "harnesses" as const, href: "/settings/harnesses", label: t("settings.tab.harnesses"), Icon: Blocks },
        { id: "memory" as const, href: "/settings/memory", label: t("settings.tab.memory"), Icon: Brain },
        { id: "search" as const, href: "/settings/search", label: t("settings.tab.search"), Icon: Search },
        { id: "voice" as const, href: "/settings/voice", label: t("settings.tab.voice"), Icon: Mic },
      ],
    },
    {
      label: text("Computers and connections", "电脑与连接"),
      tabs: [
        { id: "machines" as const, href: "/settings/machines", label: t("settings.tab.machines"), Icon: Server },
        { id: "browser" as const, href: "/settings/browser", label: t("settings.tab.browser"), Icon: ChromeIcon },
        { id: "channels" as const, href: "/settings/channels", label: t("settings.tab.channels"), Icon: RadioTower },
      ],
    },
    {
      label: text("Data", "数据"),
      tabs: [
        { id: "import" as const, href: "/settings/import", label: t("settings.tab.import"), Icon: Import },
        { id: "versions" as const, href: "/settings/versions", label: t("settings.tab.versions"), Icon: History },
      ],
    },
  ];

  return (
    <div className="main">
      <div className={styles.view} data-settings-view="">
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
            {groups.map((group) => (
              <Fragment key={group.label}>
                <RailGroupLabel>{group.label}</RailGroupLabel>
                {group.tabs.map(({ id, href, label, Icon }) => (
                  <RailItem key={id} href={href} icon={<Icon size={20} />} label={label} active={active === id} />
                ))}
              </Fragment>
            ))}
          </SecondaryRail>
          <div className="p-[0] min-w-0 min-h-0 flex flex-col">{children}</div>
        </RailSplit>
      </div>
    </div>
  );
}
