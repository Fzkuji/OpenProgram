"use client";

import { useParams } from "next/navigation";

import { ProvidersSection } from "@/components/settings/providers";

/**
 * Per-provider route: /settings/providers/<id> selects that provider in
 * the two-pane settings view, so a refresh or shared link lands on it.
 * /settings/providers (no id) keeps the list/default behaviour via the
 * sibling page.tsx.
 */
export default function Page() {
  const params = useParams<{ providerId: string }>();
  const providerId = params?.providerId;
  return (
    <ProvidersSection
      initialProviderId={providerId ? decodeURIComponent(providerId) : undefined}
    />
  );
}
