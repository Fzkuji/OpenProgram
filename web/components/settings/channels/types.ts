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

export const PLATFORM_LABEL: Record<string, string> = {
  telegram: "Telegram",
  discord: "Discord",
  slack: "Slack",
  wechat: "WeChat",
};
