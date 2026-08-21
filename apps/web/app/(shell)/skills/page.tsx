"use client";

import dynamic from "next/dynamic";
import { usePathname } from "next/navigation";

const CapabilitiesPage = dynamic(
  () => import("@/components/capabilities/capabilities-page").then((m) => m.CapabilitiesPage),
  { ssr: false },
);
const SkillDetailPage = dynamic(
  () => import("@/components/skills/skill-detail-page").then((m) => m.SkillDetailPage),
  { ssr: false },
);

// Static export has no dynamic segments: /skills/<name> is served by the
// worker's SPA fallback with this page's HTML, and the sub-path is
// resolved client-side from the pathname.
export default function Page() {
  const pathname = usePathname() || "";
  const m = pathname.match(/^\/skills\/(.+?)\/?$/);
  if (m) {
    return <SkillDetailPage name={m[1].split("/").map(decodeURIComponent).join("/")} />;
  }
  return <CapabilitiesPage />;
}
