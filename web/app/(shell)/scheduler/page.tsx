"use client";

import dynamic from "next/dynamic";

const SchedulerPage = dynamic(
  () => import("@/components/scheduler/scheduler-page").then((m) => m.SchedulerPage),
  { ssr: false },
);

export default function Page() {
  return <SchedulerPage />;
}
