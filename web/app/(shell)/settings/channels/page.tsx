"use client";

import { SettingsTabsLayout } from "@/components/settings/settings-tabs-layout";
import { ChannelsSection } from "@/components/settings/channels";

export default function Page() {
  return (
    <SettingsTabsLayout active="channels">
      <ChannelsSection />
    </SettingsTabsLayout>
  );
}
