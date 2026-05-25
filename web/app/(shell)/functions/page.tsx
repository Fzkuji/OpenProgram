"use client";

import dynamic from "next/dynamic";

// Migrated from PageShell injection of (legacy /html/(legacy programs.html) — gone). Native
// React component with co-located CSS module — no longer references
// the global `.pg-*` classes from app/styles/04-functions.css.
const FunctionsPage = dynamic(
  () => import("@/components/functions/functions-page").then((m) => m.FunctionsPage),
  { ssr: false },
);

export default function Page() {
  return <FunctionsPage />;
}
