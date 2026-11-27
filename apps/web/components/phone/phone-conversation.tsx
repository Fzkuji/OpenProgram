"use client";

/**
 * A conversation on the phone, laid out as a Messages thread: Framework7's
 * bar with Back, the title and its state, and ⓘ for its details; then the
 * transcript and the composer
 * (the split view's self-contained session pane, which the composer lays
 * out for a phone itself: composer/controls/phone-composer-bar.tsx).
 */

import { useEffect, useRef } from "react";
import { Info } from "lucide-react";

import { PeerSessionPane } from "@/components/chat/pane/peer-session-pane";
import { activityState } from "@/components/sidebar/sessions/activity";
import { displayTitle, type LegacyConv } from "@/components/sidebar/sessions/helpers";
import { useTranslation } from "@/lib/i18n";
import { useSessionStore } from "@/lib/session-store";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";

import { Link, Navbar, Page } from "framework7-react";

import { PhoneMessageMenu } from "./phone-message-menu";
import { openDetails } from "./phone-nav";
import { backTo } from "./phone-router";

/** A step tapped in the transcript opens as a page of its own: the
 *  desktop puts it in the right panel, which a phone does not have. */
function useStepPage(sessionId: string | null) {
  const node = useSessionStore((s) => s.detailNode);
  const seen = useRef(node);
  useEffect(() => {
    if (node === seen.current) return;
    seen.current = node;
    if (node && sessionId) openDetails(sessionId, "step");
  }, [node, sessionId]);
}

export function PhoneConversation({ sessionId }: { sessionId: string | null }) {
  const { text } = useTranslation();
  const tab = useCenterTabs((s) => sessionId
    ? s.tabs.find((t) => t.kind === "session" && t.sessionId === sessionId)
    : s.tabs.find((t) => t.id === s.activeId && t.kind === "session"));
  const sid = sessionId ?? tab?.sessionId ?? null;
  const conv = useSessionStore((s) => sid ? s.conversations[sid] : undefined) as LegacyConv | undefined;
  const running = useSessionStore((s) => !!(sid && s.runningTasks[sid]));
  const state = conv ? activityState(conv, running) : running ? "working" : null;
  const saved = !!conv && !tab?.draft;
  const root = useRef<HTMLDivElement>(null);
  useStepPage(sid);
  const status = state === "needs_input" ? text("Waiting for you", "等你确认")
    : state === "working" ? text("Running", "运行中")
    : state === "failed" ? text("Failed", "失败")
    : null;

  return (
    <Page pageContent={false} className="phone-conversation" data-state={state ?? undefined}>
      <Navbar
        // The page under it: the conversation's project, as an iOS
        // navigation bar names the page Back returns to; a long name
        // would crowd the title, so it is "Back" then, as on iOS.
        {...backTo(conv?.project && [...conv.project].length <= 10 ? conv.project : text("Back", "返回"))}
        title={(conv && displayTitle(conv)) || text("New chat", "新对话")}
        subtitle={status ?? undefined}
      >
        {saved && sid ? (
          <Link slot="right" iconOnly aria-label={text("Details", "详情")} onClick={() => openDetails(sid)}>
            <Info size={22} aria-hidden="true" />
          </Link>
        ) : null}
      </Navbar>
      {/* The transcript and the composer are the app's own; the composer's
          controls are Framework7's again (phone-composer-bar.tsx). */}
      <div className="phone-conversation-body" ref={root} data-not-f7>
        <PhoneMessageMenu root={root}>
          {tab && sid ? (
            <PeerSessionPane key={sid} tabId={tab.id} sessionId={sid} title={tab.title} showTitle={false} />
          ) : <div className="phone-app-fill" />}
        </PhoneMessageMenu>
      </div>
    </Page>
  );
}
