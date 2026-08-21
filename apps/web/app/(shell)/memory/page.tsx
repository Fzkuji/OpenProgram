"use client";

import dynamic from "next/dynamic";

const HistoryPage = dynamic(
  () => import("@/components/history/history-page").then((m) => m.HistoryPage),
  { ssr: false },
);

export default function Page() {
  return <HistoryPage />;
}
