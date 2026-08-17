"use client";

import dynamic from "next/dynamic";

const ProgramsPage = dynamic(
  () => import("@/components/functions/functions-page").then((m) => m.FunctionsPage),
  { ssr: false },
);

export default function Page() {
  return <ProgramsPage />;
}
