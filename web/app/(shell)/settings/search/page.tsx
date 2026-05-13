"use client";

import dynamic from "next/dynamic";
import { SettingsTabsLayout } from "@/components/settings/settings-tabs-layout";

const SearchProvidersSection = dynamic(
  () =>
    import("@/components/settings/search-providers-section").then(
      (m) => m.SearchProvidersSection,
    ),
  { ssr: false },
);

export default function Page() {
  return (
    <SettingsTabsLayout active="search">
      <SearchProvidersSection />
    </SettingsTabsLayout>
  );
}
