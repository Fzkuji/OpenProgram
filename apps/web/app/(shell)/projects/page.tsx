"use client";

import dynamic from "next/dynamic";

const ProjectsPage = dynamic(
  () => import("@/components/projects/projects-page").then((m) => m.ProjectsPage),
  { ssr: false },
);

export default function Page() {
  return <ProjectsPage />;
}
