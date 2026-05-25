"use client";

import dynamic from "next/dynamic";

const SkillsPage = dynamic(
  () => import("@/components/skills/skills-page").then((m) => m.SkillsPage),
  { ssr: false },
);

export default function Page() {
  return <SkillsPage />;
}
