"use client";

/**
 * A QR code for a link, drawn as one SVG path in the text colour on the
 * card's background, so it follows the theme and stays sharp at any size.
 * Encoding is `uqr`; nothing leaves the page.
 */
import { useMemo } from "react";
import { encode } from "uqr";

import { cn } from "@/lib/utils";

export function QrCode({ value, label, className }: { value: string; label: string; className?: string }) {
  const { size, path } = useMemo(() => {
    const { data, size } = encode(value, { ecc: "M", border: 1 });
    let d = "";
    data.forEach((row, y) => row.forEach((dark, x) => {
      if (dark) d += `M${x} ${y}h1v1h-1z`;
    }));
    return { size, path: d };
  }, [value]);
  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${size} ${size}`}
      shapeRendering="crispEdges"
      className={cn("block aspect-square rounded-md bg-white text-black", className)}
    >
      <path d={path} fill="currentColor" />
    </svg>
  );
}
