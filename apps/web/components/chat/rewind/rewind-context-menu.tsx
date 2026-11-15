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
  if (!enabled || !sessionId || !isRewindable(msg) || !inConversation) return children;
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild onContextMenuCapture={keepNativeMenuForSelection}>
        {children}
      </ContextMenuTrigger>
      <ContextMenuContent>
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
      </ContextMenuContent>
    </ContextMenu>
  );
}
