"use client";

import dynamic from "next/dynamic";
import { useParams } from "next/navigation";

const ProjectDetailPage = dynamic(
  () => import("@/components/projects/project-detail-page").then((m) => m.ProjectDetailPage),
  { ssr: false },
);

export default function Page() {
  const params = useParams();
  const projectId = String(params?.projectId || "");
  return <ProjectDetailPage projectId={projectId} />;
}
