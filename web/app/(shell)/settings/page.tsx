"use client";

import dynamic from "next/dynamic";

// Migrated from PageShell injection of /html/settings.html. Native
// React component with co-located CSS module.
const SettingsPage = dynamic(
  () =>
    import("@/components/settings/settings-page").then((m) => m.SettingsPage),
  { ssr: false },
);

export default function Page() {
  return <SettingsPage />;
}
