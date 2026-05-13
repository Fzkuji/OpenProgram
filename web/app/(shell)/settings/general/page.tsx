"use client";

import dynamic from "next/dynamic";
import { SettingsTabsLayout } from "@/components/settings/settings-tabs-layout";

const GeneralSection = dynamic(
  () =>
    import("@/components/settings/general-section").then(
      (m) => m.GeneralSection,
    ),
  { ssr: false },
);

export default function Page() {
  return (
    <SettingsTabsLayout active="general">
      <GeneralSection />
    </SettingsTabsLayout>
  );
}
