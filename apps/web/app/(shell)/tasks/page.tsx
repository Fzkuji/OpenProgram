"use client";

import dynamic from "next/dynamic";

const TasksPage = dynamic(
  () => import("@/components/tasks/tasks-page").then((m) => m.TasksPage),
  { ssr: false },
);

export default function Page() {
  return <TasksPage />;
}
