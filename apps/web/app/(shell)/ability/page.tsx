"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import dynamic from "next/dynamic";

const CapabilitiesPage = dynamic(
  () => import("@/components/capabilities/capabilities-page").then((m) => m.CapabilitiesPage),
  { ssr: false },
);

export default function Page() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/plugins");
  }, [router]);
  return <CapabilitiesPage />;
}
