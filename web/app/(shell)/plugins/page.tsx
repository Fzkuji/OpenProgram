"use client";

import dynamic from "next/dynamic";

const PluginsPage = dynamic(
  () => import("@/components/plugins/plugins-page").then((m) => m.PluginsPage),
  { ssr: false },
);

export default function Page() {
  return <PluginsPage />;
}
