"use client";

import { SettingsTabsLayout } from "@/components/settings/settings-tabs-layout";

/**
 * Real Next.js layout for /settings/*. Previously each page.tsx
 * rendered its own <SettingsTabsLayout>, which made the topbar + nav
 * tear down and rebuild on every tab click — that was the dominant
 * cause of "even General is slow" lag. With this file, the shell
 * mounts once when the user enters /settings and stays mounted as
 * they click between subpages; only the section body inside swaps.
 */
export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <SettingsTabsLayout>{children}</SettingsTabsLayout>;
}
