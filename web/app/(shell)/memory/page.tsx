"use client";

import dynamic from "next/dynamic";

const MemoryPage = dynamic(
  () => import("@/components/memory/memory-page").then((m) => m.MemoryPage),
  { ssr: false },
);

export default function Page() {
  return <MemoryPage />;
}
