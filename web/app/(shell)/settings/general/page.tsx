"use client";

import { SettingsTabsLayout } from "@/components/settings/settings-tabs-layout";
import { GeneralSection } from "@/components/settings/general-section";

export default function Page() {
  return (
    <SettingsTabsLayout active="general">
      <GeneralSection />
    </SettingsTabsLayout>
  );
}
