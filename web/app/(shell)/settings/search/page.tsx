"use client";

import { SettingsTabsLayout } from "@/components/settings/settings-tabs-layout";
import { SearchProvidersSection } from "@/components/settings/search-providers";

export default function Page() {
  return (
    <SettingsTabsLayout active="search">
      <SearchProvidersSection />
    </SettingsTabsLayout>
  );
}
