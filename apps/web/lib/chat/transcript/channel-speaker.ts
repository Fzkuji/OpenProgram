/** Who sent a message that came in through a chat channel (WeChat,
 *  Telegram, Discord, Slack). The worker keeps the sender beside the
 *  text, never inside it; turns stored before that carried a
 *  "[name (id)] " label at the head of the text, which is stripped here
 *  for display. */

import { CHANNEL_BRANDS } from "../../channels/channel-brands.ts";

type Text = (en: string, zh: string) => string;

export function isChannelSource(source: string | undefined): source is string {
  return Boolean(source && source in CHANNEL_BRANDS);
}

export function channelName(channel: string, text: Text): string {
  return channel === "wechat" ? text("WeChat", "微信") : CHANNEL_BRANDS[channel]?.name ?? channel;
}

export interface ChannelSpeaker {
  id?: string;
  display?: string;
}

/** A platform id with no nickname behind it reads as noise
 *  ("oTestUser0000000000000000000@im.wechat"): drop the account suffix
 *  and keep both ends, enough to tell two senders apart. */
export function shortSpeakerId(id: string): string {
  const bare = id.replace(/@im\.wechat$/, "");
  return bare.length > 12 ? `${bare.slice(0, 4)}…${bare.slice(-4)}` : bare;
}

export function speakerName(speaker: ChannelSpeaker | undefined): string {
  const display = speaker?.display?.trim();
  const id = speaker?.id?.trim();
  if (display && display !== id) return display;
  return id ? shortSpeakerId(id) : "";
}

/** The text without a leading "[label] " that names this same sender. */
export function stripLegacySpeakerLabel(content: string, speaker: ChannelSpeaker | undefined): string {
  const match = /^\[([^\]\n]+)\] /.exec(content);
  if (!match || !speaker) return content;
  const label = match[1];
  const { id, display } = speaker;
  const known = [display, id, display && id ? `${display} (${id})` : undefined];
  return known.includes(label) ? content.slice(match[0].length) : content;
}

export interface RoomHistoryLine {
  speaker: string;
  content: string;
}

const ROOM_HISTORY_HEADER = "[earlier messages in this chat]\n";

/** Group messages the bot was not addressed in reach the next turn as a
 *  block ahead of the text: a header line, then one "> {json}" line per
 *  message. Returns those messages and the text after the block. */
export function splitRoomHistory(content: string): { history: RoomHistoryLine[]; rest: string } {
  if (!content.startsWith(ROOM_HISTORY_HEADER)) return { history: [], rest: content };
  const end = content.indexOf("\n\n", ROOM_HISTORY_HEADER.length);
  const block = content.slice(ROOM_HISTORY_HEADER.length, end < 0 ? undefined : end);
  const history: RoomHistoryLine[] = [];
  for (const line of block.split("\n")) {
    if (!line.startsWith("> ")) return { history: [], rest: content };
    try {
      const parsed = JSON.parse(line.slice(2)) as { speaker?: unknown; content?: unknown };
      history.push({
        speaker: typeof parsed.speaker === "string" ? parsed.speaker : "",
        content: typeof parsed.content === "string" ? parsed.content : "",
      });
    } catch {
      return { history: [], rest: content };
    }
  }
  return { history, rest: end < 0 ? "" : content.slice(end + 2) };
}

const ROOM_EVENT_HEADER = "[room event]\n";

/** A group message that did not address the bot, in a room set to
 *  ambient: the worker marks it with a first line the agent reads.
 *  Returns whether the mark is there and the text after it. */
export function splitRoomEvent(content: string): { roomEvent: boolean; rest: string } {
  return content.startsWith(ROOM_EVENT_HEADER)
    ? { roomEvent: true, rest: content.slice(ROOM_EVENT_HEADER.length) }
    : { roomEvent: false, rest: content };
}
