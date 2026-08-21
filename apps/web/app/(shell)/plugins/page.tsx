"use client";

import dynamic from "next/dynamic";

const CapabilitiesPage = dynamic(
  () => import("@/components/capabilities/capabilities-page").then((m) => m.CapabilitiesPage),
  { ssr: false },
);

export default function Page() {
  return <CapabilitiesPage />;
}
