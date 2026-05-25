"use client";

import dynamic from "next/dynamic";
import { SettingsTabsLayout } from "@/components/settings/settings-tabs-layout";

const ProvidersSection = dynamic(
  () =>
    import("@/components/settings/providers").then(
      (m) => m.ProvidersSection,
    ),
  { ssr: false },
);

export default function Page() {
  return (
    <SettingsTabsLayout active="providers">
      <ProvidersSection />
    </SettingsTabsLayout>
  );
}
