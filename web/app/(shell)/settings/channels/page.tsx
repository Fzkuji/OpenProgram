"use client";

import dynamic from "next/dynamic";
import { SettingsTabsLayout } from "@/components/settings/settings-tabs-layout";

const ChannelsSection = dynamic(
  () =>
    import("@/components/settings/channels").then((m) => m.ChannelsSection),
  { ssr: false },
);

export default function Page() {
  return (
    <SettingsTabsLayout active="channels">
      <ChannelsSection />
    </SettingsTabsLayout>
  );
}
