"use client";

import dynamic from "next/dynamic";

const PendingPage = dynamic(
  () => import("@/components/pending/pending-page").then((m) => m.PendingPage),
  { ssr: false },
);

export default function Page() {
  return <PendingPage />;
}
