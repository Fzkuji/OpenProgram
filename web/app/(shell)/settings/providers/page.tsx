"use client";

import { SettingsTabsLayout } from "@/components/settings/settings-tabs-layout";
import { ProvidersSection } from "@/components/settings/providers";

// Direct import (not next/dynamic). The previous lazy load left the
// content slot blank while the chunk was being fetched/parsed, so the
// page showed its shell + empty body for a noticeable beat after each
// tab click. Pulling the section into the route bundle removes that
// empty-content flash; the section's own useEffect-driven fetch is
// what fills in data progressively after mount.
export default function Page() {
  return (
    <SettingsTabsLayout active="providers">
      <ProvidersSection />
    </SettingsTabsLayout>
  );
}
