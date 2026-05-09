"use client";

import dynamic from "next/dynamic";

// Migrated from PageShell injection of /html/programs.html. Native
// React component with co-located CSS module — no longer references
// the global `.pg-*` classes from app/styles/04-programs.css.
const ProgramsPage = dynamic(
  () => import("@/components/programs/programs-page").then((m) => m.ProgramsPage),
  { ssr: false },
);

export default function Page() {
  return <ProgramsPage />;
}
