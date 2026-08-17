"use client";

import dynamic from "next/dynamic";

const ProgramsPage = dynamic(
  () => import("@/components/programs/programs-page").then((m) => m.ProgramsPage),
  { ssr: false },
);

export default function Page() {
  return <ProgramsPage />;
}
