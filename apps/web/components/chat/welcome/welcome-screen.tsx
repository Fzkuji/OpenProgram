/**
 * WelcomeScreen — the empty chat.
 *
 * No mark or tagline: the composer floats over an empty area, or over the
 * code rain when the "Code rain style" setting is on. An empty chat with an
 * always-on Agent spells ALWAYS-ON in glyphs (always-on-intro.tsx) when the
 * chat switches to that Agent, then stays empty like every other chat.
 * Visible whenever the session store says so; mounted as a portal inside
 * the #welcome-mount placeholder that PageShell leaves in the chat area.
 * When the chat sends its first message the rain is kept a moment longer
 * so it can stop and fade instead of vanishing; when another chat opens
 * instead, the rain goes at once rather than falling over its messages.
 */
"use client";

import { memo, useEffect, useState } from "react";

import { useConversationAgent } from "@/lib/chat/transcript/use-bot-view";
import { useSessionStore } from "@/lib/session-store";

import { AlwaysOnIntro } from "./always-on-intro";
import { WelcomeBackdrop } from "./welcome-backdrop";

const STOP_MS = 1200;

/** The Agent comes from the saved list, as the bot view decides it, so the
 *  default Agent counts. It plays only when the open chat's Agent changes to
 *  an always-on one; opening or coming back to a chat, a new chat, or the
 *  Agents list arriving does not. A trial runs unsaved settings once, so it
 *  gets no intro. */
function AlwaysOnWelcome() {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const chatKey = useSessionStore((s) => s.activeChatKey);
  const agent = useConversationAgent(sessionId, chatKey);
  const trial = useSessionStore((s) => Boolean(chatKey && s.composerSettingsBySession[chatKey]?.agentInvocation?.trial));
  const chat = chatKey ?? sessionId;
  const agentId = agent?.id ?? null;
  const [last, setLast] = useState({ chat, agentId });
  const [plays, setPlays] = useState(0);
  if (last.chat !== chat || last.agentId !== agentId) {
    // A new chat getting its own draft key is still the same chat.
    const sameChat = last.chat === chat || (last.chat === null && Boolean(chat?.startsWith("local_")));
    const switched = sameChat && last.agentId !== null && agentId !== null && last.agentId !== agentId;
    const keep = sameChat && last.agentId === agentId;
    setLast({ chat, agentId });
    setPlays((n) => (switched ? n + 1 : keep ? n : 0));
  }
  if (!plays || !agent?.always_on?.enabled || trial) return null;
  return (
    <div className="relative z-[1] h-full w-full">
      <AlwaysOnIntro key={plays} />
    </div>
  );
}

// Memoized: the shell redraws on every tab and route change, and this takes no part in those.
export const WelcomeScreen = memo(function WelcomeScreen() {
  const visible = useSessionStore((s) => s.welcomeVisible);
  const fade = useSessionStore((s) => s.welcomeFade);
  const [stopping, setStopping] = useState(false);
  const [wasVisible, setWasVisible] = useState(visible);
  if (visible !== wasVisible) {
    setWasVisible(visible);
    setStopping(!visible && fade);
  }
  useEffect(() => {
    if (!stopping) return;
    const timer = setTimeout(() => setStopping(false), STOP_MS);
    return () => clearTimeout(timer);
  }, [stopping]);

  // The canvas is absolutely placed in .chat-area, so without the
  // flex-filling .welcome wrapper it takes no room from the messages.
  if (!visible) return stopping ? <WelcomeBackdrop stopping /> : null;

  return (
    <div className="flex-1 w-full">
      <WelcomeBackdrop />
      <AlwaysOnWelcome />
    </div>
  );
});
