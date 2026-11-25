"use client";

/** The right-click menu of a user message: Rewind to here and Fork from
 *  here. With text selected the browser's own menu stays, for copying. */
import type { MouseEvent, ReactElement } from "react";

import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { MenuOptionContent } from "@/components/ui/menu/option-content";
import { ITEM_TWO_LINE } from "@/components/ui/menu/menu-styles";
import { useSessionStore, type ChatMsg } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { nativeMenus } from "@/lib/mobile/native-menu";
import { forkFromMessage, requestRewind } from "@/lib/chat/rewind/rewind-actions";
import { forkDisabledReason, isRewindable } from "@/lib/chat/rewind/rewind-logic";
import { useForkDisabled, useRewindAllowed, useRewindDisabled } from "@/lib/chat/rewind/use-rewind-disabled";

function keepNativeMenuForSelection(event: MouseEvent): void {
  const selection = window.getSelection?.();
  if (selection && !selection.isCollapsed) event.stopPropagation();
}

export function RewindContextMenu({ msg, sessionId, enabled, children }: {
  msg: ChatMsg;
  sessionId: string | null | undefined;
  enabled: boolean;
  children: ReactElement;
}) {
  const { text } = useTranslation();
  const cause = useRewindDisabled(sessionId);
  const allowed = useRewindAllowed(sessionId);
  const forkCause = useForkDisabled(sessionId, msg.id);
  const forkReason = forkCause ? forkDisabledReason(forkCause, text) : undefined;
  const inConversation = useSessionStore(s => Boolean(sessionId && s.messageOrder?.[sessionId]?.includes(msg.id)));
  // The message is wrapped the same way whether or not it has a menu yet:
  // a send gets its menu when the server acknowledges it, and a wrapper
  // that appeared then would mount the bubble a second time. Until then
  // the trigger is off and the browser's own menu shows.
  // In the phone app the long press is iOS's menu, which lists both.
  const offered = enabled && !!sessionId && isRewindable(msg) && inConversation && !nativeMenus();
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild disabled={!offered} onContextMenuCapture={keepNativeMenuForSelection}>
        {children}
      </ContextMenuTrigger>
      {!offered ? null : <ContextMenuContent>
        {cause || !allowed ? null : (
          <ContextMenuItem className={ITEM_TWO_LINE} onSelect={() => void requestRewind(sessionId, msg.id, msg.content ?? "")}>
            <MenuOptionContent
              label={text("Rewind to here", "回退到这里")}
              description={text("This message and what follows leave the conversation", "这条消息及之后的内容离开对话")}
            />
          </ContextMenuItem>
        )}
        <ContextMenuItem
          className={ITEM_TWO_LINE}
          disabled={Boolean(forkReason)}
          onSelect={() => void forkFromMessage(sessionId, msg.id)}
        >
          <MenuOptionContent
            label={text("Fork from here", "从这里分叉")}
            description={forkReason ?? text("Starts a new conversation, keeps this one", "开启新对话，保留这段对话")}
          />
        </ContextMenuItem>
      </ContextMenuContent>}
    </ContextMenu>
  );
}
