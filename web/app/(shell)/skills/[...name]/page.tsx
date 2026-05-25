"use client";

import dynamic from "next/dynamic";

const SkillDetailPage = dynamic(
  () => import("@/components/skills/skill-detail-page").then((m) => m.SkillDetailPage),
  { ssr: false },
);

export default function Page({ params }: { params: { name: string[] } }) {
  const name = (params.name || []).join("/");
  return <SkillDetailPage name={name} />;
}
