/**
 * The model chip's icon: the logo of the provider the model is called
 * through (OpenAI, Anthropic, OpenRouter...), the same logos the
 * Settings provider list shows, drawn by BrandMark; a harness (Codex)
 * shows its maker's. Providers without a
 * logo (custom, local, generic OpenAI-compatible endpoints) show the chip
 * glyph, which says "a model" without naming a maker it may not be.
 *
 * It sits in the composer (the model chip and the model menu's provider
 * rows), so both the logo and the glyph fill their 16 px box and grow to
 * 18 px on hover (fit="fill").
 */
"use client";

import { BrandMark } from "@/components/brand-mark";
import { SolarIcon } from "@/components/solar-icons";
import { providerLogo } from "@/components/settings/providers/icons/provider-icon";
import { agentLogo, harnessMark } from "@/lib/models/harnesses";
import { cn } from "@/lib/utils";

/** Shown when the provider has no logo. */
export const MODEL_FALLBACK_ICON = "cpu";

export function ModelMark({ provider, size = 16, className }: { provider?: string; size?: number; className?: string }) {
  const logo = providerLogo(harnessMark(provider));
  if (!logo) {
    return <SolarIcon name={MODEL_FALLBACK_ICON} size={size} fit="fill" className={cn("shrink-0", className)} aria-hidden="true" />;
  }
  return <BrandMark logo={logo} size={size} fit="fill" className={className} attributes={{ "data-provider-logo": provider }} />;
}

/** An outside agent's own logo (Codex, Claude Code), for where the agent
 *  rather than its model's maker is meant: the Harness chip and its menu. */
export function AgentMark({ harness, size = 16, fit = "fill", className }: {
  harness?: string | null; size?: number; fit?: "fill" | "contain"; className?: string;
}) {
  const logo = providerLogo(agentLogo(harness));
  if (!logo) return <ModelMark provider={harness ?? undefined} size={size} className={className} />;
  return <BrandMark logo={logo} size={size} fit={fit} className={className} attributes={{ "data-agent-logo": harness ?? undefined }} />;
}
