/**
 * WelcomeScreen — the empty chat.
 *
 * No mark or tagline: the composer floats over an empty area, or over the
 * code rain when the "Code rain style" setting is on. An empty chat with an
 * always-on Agent also offers three things to say to it, until the box has
 * text; every other chat stays empty.
 * Visible whenever the session store says so; mounted as a portal inside
 * the #welcome-mount placeholder that PageShell leaves in the chat area.
 * When the chat sends its first message the rain is kept a moment longer
 * so it can stop and fade instead of vanishing; when another chat opens
 * instead, the rain goes at once rather than falling over its messages.
 */
"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { useConversationAgent } from "@/lib/chat/transcript/use-bot-view";
import { useTranslation } from "@/lib/i18n";
import { useSessionStore } from "@/lib/session-store";

import { WelcomeBackdrop } from "./welcome-backdrop";

const STOP_MS = 1200;

const STARTERS = [
  ["Keep an eye on this project and tell me when something needs me.", "帮我盯着这个项目，有需要我处理的事就告诉我。"],
  ["Remember: only message me about things I have to act on.", "记住：只有需要我动手的事才发消息给我。"],
  ["What are you keeping track of for me right now?", "你现在在替我留意哪些事？"],
];

/** Clicking a starter only fills the box, so the person can edit it first.
 *  The Agent comes from the saved list, as the bot view decides it, so the
 *  default Agent counts and turning always-on off takes effect at once. A
 *  trial runs unsaved settings once and cannot remember, so it gets none. */
function AlwaysOnStarters() {
  const { text } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const chatKey = useSessionStore((s) => s.activeChatKey);
  const agent = useConversationAgent(sessionId, chatKey);
  const trial = useSessionStore((s) => Boolean(chatKey && s.composerSettingsBySession[chatKey]?.agentInvocation?.trial));
  const hasDraft = useSessionStore((s) => Boolean(s.composerDrafts[chatKey ?? sessionId ?? "__new__"]?.trim()));
  if (!agent?.always_on?.enabled || trial || hasDraft) return null;
  const use = (value: string) => {
    const store = useSessionStore.getState();
    store.setComposerInputFor(chatKey ?? sessionId, value);
    store.focusComposer();
  };
  return (
    <div className="relative z-[1] flex h-full w-full flex-col items-center justify-end gap-[8px] px-[16px] pb-[16px]">
      <p className="text-fs-sm text-muted-foreground">
        {text(
          `${agent.name || "This Agent"} keeps running between messages. Pick one to put it in the box, then edit and send it.`,
          `${agent.name || "这个 Agent"}会一直在后台运行。点一句放进输入框，改好后再发送。`,
        )}
      </p>
      {STARTERS.map((pair) => {
        const starter = text(...pair);
        return (
          <Button key={pair[0]} variant="elevated" size="sm" className="max-w-full" onClick={() => use(starter)}>
            <span className="truncate">{starter}</span>
          </Button>
        );
      })}
    </div>
  );
}

export function WelcomeScreen() {
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
      <AlwaysOnStarters />
    </div>
  );
}
