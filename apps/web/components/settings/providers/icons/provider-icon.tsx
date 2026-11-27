"use client";

import { BrandMark, type BrandLogo } from "@/components/brand-mark";

import icons from "./provider-icons.json";
import styles from "../provider-pane.module.css";

export type ProviderLogo = BrandLogo;

/** The brand logo shipped for a provider id, or null when there is none
 *  (custom, local and generic OpenAI-compatible providers). */
export function providerLogo(id: string | undefined): ProviderLogo | null {
  const icon = id ? (icons as Record<string, { file: string; dark?: string; mono: boolean }>)[id] : undefined;
  return icon ? {
    url: `/images/providers/${icon.file}`, mono: icon.mono,
    ...(icon.dark ? { darkUrl: `/images/providers/${icon.dark}` } : {}),
  } : null;
}

/** The plate's padding around the logo, in px (provider-pane.module.css). */
const PLATE_PADDING = 2;
/** Around the logo on a brand plate (the 40px tiles), in px. */
const BRAND_PLATE_PADDING = 7;

/** The plate a brand draws its mark on: white, or black for a mark whose
 *  ink is white (provider-icons.json "plate": "dark"). */
export function brandPlate(id: string | undefined): "light" | "dark" {
  const icon = id ? (icons as Record<string, { plate?: string }>)[id] : undefined;
  return icon?.plate === "dark" ? "dark" : "light";
}

/** A provider's logo on the tinted plate Settings lists it with, which
 *  keeps mono marks visible on every theme: the whole logo fitted inside
 *  the plate's padding, still on hover. Unknown and custom providers keep
 *  an initial. With `plate="brand"` the plate is the brand's own (white or
 *  black, the same on every theme) and a mono mark is inked to match. */
export function ProviderIcon({ id, size = 24, plate }: { id: string; size?: number; plate?: "brand" }) {
  const icon = providerLogo(id);
  const dimensions = { width: size, height: size };
  const brand = plate === "brand" ? brandPlate(id) : undefined;
  if (icon) {
    const ink = brand ? (brand === "dark" ? "white" : "black") : "var(--text-primary)";
    return (
      <span className={styles.providerIcon} style={dimensions} data-plate={brand} aria-hidden="true">
        {/* A brand plate is the same on every theme, so its logo is too. */}
        <BrandMark logo={brand ? { url: icon.url, mono: icon.mono } : icon}
          size={size - 2 * (brand ? BRAND_PLATE_PADDING : PLATE_PADDING)} color={ink} lazy />
      </span>
    );
  }
  return (
    <span className={styles.providerIconLetter} style={dimensions}>
      {(id[0] || "?").toUpperCase()}
    </span>
  );
}
