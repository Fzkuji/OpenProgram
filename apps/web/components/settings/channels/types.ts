import { CHANNEL_BRANDS } from "@/lib/channels/channel-brands";

export interface ChannelAccount {
  channel: string;
  account_id: string;
  name: string;
  enabled: boolean;
  configured: boolean;
}

export interface ChannelBindingMatch {
  channel?: string;
  account_id?: string;
  peer?: {
    kind?: string;
    id?: string;
  };
}

export interface ChannelBinding {
  id: string;
  agent_id: string;
  match: ChannelBindingMatch;
  created_at?: number;
}

export const PLATFORMS = [
  "telegram",
  "discord",
  "slack",
  "wechat",
] as const;
export type Platform = (typeof PLATFORMS)[number];

/** Platforms that have group chats (WeChat bots only get direct messages). */
export const GROUP_PLATFORMS: string[] = ["telegram", "discord", "slack"];

/** Platform display names, from the one channel brand map. */
export const PLATFORM_LABEL: Record<string, string> = Object.fromEntries(
  Object.entries(CHANNEL_BRANDS).map(([id, brand]) => [id, brand.name]),
);

export interface ChannelHealthStatus {
  alive: boolean;
  state: "alive" | "stale" | "unknown" | "off" | "no_token" | "signed_out" | "failed" | "reconnecting";
  last_seen_at: number | null;
  age_seconds: number | null;
}

export type StatusMap = Record<string, ChannelHealthStatus>;

export interface ChannelAccessSender {
  user_id: string;
  display: string;
  code?: string;
  requested_at?: number;
  approved_at?: number;
}

export interface ChannelAccessAccount {
  channel: string;
  account_id: string;
  paired: ChannelAccessSender[];
  pending: Array<ChannelAccessSender & { code: string }>;
  group_full_access: ChannelAccessSender[];
  group_chat_only: ChannelAccessSender[];
}
