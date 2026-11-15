"use client";

import { BrandMark, type BrandMarkFit } from "@/components/brand-mark";
import { SolarIcon } from "@/components/solar-icons";
import { channelBrandInfo } from "@/lib/channels/channel-brands";

/** A chat channel's brand mark in its brand colour (WeChat green,
 *  Telegram blue, Discord blurple, Slack's four colours), drawn by
 *  BrandMark. A channel without a shipped logo shows the chat glyph in the
 *  text colour. `fit="fill"` is for the composer and its popups: the
 *  mark's ink fills the 16 px box and grows to 18 px on hover; everywhere
 *  else the whole logo is fitted into the box and stays still. */
export function ChannelLogo({
  channel,
  size = 14,
  fit = "contain",
  className,
}: {
  channel?: string | null;
  size?: number;
  fit?: BrandMarkFit;
  className?: string;
}) {
  const brand = channelBrandInfo(channel);
  if (!brand) {
    return <SolarIcon name="chat-round-dots" size={size} fit={fit === "fill" ? "fill" : "viewBox"} aria-hidden="true" className={className} />;
  }
  return (
    <BrandMark
      logo={{ url: brand.logo, mono: brand.color !== null }}
      color={brand.color ?? undefined}
      size={size}
      fit={fit}
      className={className}
      attributes={{ "data-channel-logo": channel ?? "" }}
    />
  );
}
