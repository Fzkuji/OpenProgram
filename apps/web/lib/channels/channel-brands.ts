/**
 * The chat channels a conversation can be mirrored to (WeChat, Telegram,
 * Discord, Slack): their display names, brand colours and logo files.
 * The one place the brand map lives; the sidebar rows, the composer's
 * channel chip and its menu, Settings → Channels and the status badge
 * all read it.
 *
 * Logos are simple-icons' official marks (public/images/channels,
 * NOTICE.txt). A one-colour mark is drawn through a mask in its brand
 * colour; a mark with its own colours (Slack) is drawn as is.
 */

export interface ChannelBrand {
  /** Display name, the same in every language. */
  name: string;
  /** Where the SVG is served. */
  logo: string;
  /** Fill for a one-colour mark; null when the SVG carries its own colours. */
  color: string | null;
}

export const CHANNEL_BRANDS: Record<string, ChannelBrand> = {
  wechat: { name: "WeChat", logo: "/images/channels/wechat.svg", color: "#07C160" },
  telegram: { name: "Telegram", logo: "/images/channels/telegram.svg", color: "#26A5E4" },
  discord: { name: "Discord", logo: "/images/channels/discord.svg", color: "#5865F2" },
  slack: { name: "Slack", logo: "/images/channels/slack.svg", color: null },
};

/** The brand entry for a channel id, or null for an unknown channel. */
export function channelBrandInfo(channel?: string | null): ChannelBrand | null {
  if (!channel) return null;
  return CHANNEL_BRANDS[String(channel).toLowerCase()] ?? null;
}

/** A channel's display name: the brand name, or the id itself when the
 *  channel is not one of the known apps; "" for no channel. */
export function channelBrand(channel?: string | null): string {
  if (!channel) return "";
  return channelBrandInfo(channel)?.name ?? String(channel);
}

/** What the composer's channel chip says for a binding: the account
 *  alone (the logo beside it names the app; the brand when no account
 *  is set), and the brand with the account for its tooltip. Both are
 *  null while unbound, which the chip shows as "No channel". */
export function channelChipText(channel: string | null, account: string | null) {
  if (!channel) return { label: null, tip: null };
  const brand = channelBrand(channel);
  return { label: account || brand, tip: account ? `${brand} · ${account}` : brand };
}
