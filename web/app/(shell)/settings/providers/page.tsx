"use client";

import { usePathname } from "next/navigation";

import { ProvidersSection } from "@/components/settings/providers";

/**
 * /settings/providers and /settings/providers/<id> (the latter served by
 * the worker's SPA fallback under static export — the id is resolved
 * client-side from the pathname) so a refresh or shared link lands on
 * that provider in the two-pane view.
 */
export default function Page() {
  const pathname = usePathname() || "";
  const m = pathname.match(/^\/settings\/providers\/([^/]+)/);
  return (
    <ProvidersSection
      initialProviderId={m ? decodeURIComponent(m[1]) : undefined}
    />
  );
}
