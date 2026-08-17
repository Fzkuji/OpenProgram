"use client";

/**
 * Which chat a composer chip acts on.
 *
 * These chips (project, working dirs, channel) render once per composer, and
 * a split view has one composer per pane. Reading `currentSessionId` /
 * `activeChatKey` off the global store makes every pane's chip act on the
 * *focused* session instead of its own — clicking the background pane's
 * project chip either did nothing or repointed the wrong conversation.
 *
 * Inside a `SessionScopeProvider` the enclosing scope answers instead. Its
 * `sid` is the chat key: a real backend session id, or the provisional
 * `local_*` / `__new__` key an unsent draft uses. That single value splits
 * back into the two fields the chips want — `sessionId` (null until the
 * backend has a session) and `chatKey` (null only for a never-touched new
 * chat), matching what the global pair used to hold.
 *
 * Outside a provider the global store still answers, so any unscoped call
 * site keeps its old behaviour.
 */
import { useSessionStore } from "@/lib/session-store";
import { useOptionalScopedSessionId } from "@/lib/session-store/session-scope";

export interface BoundChat {
  /** Real backend session, or null while the chat is still a draft. */
  sessionId: string | null;
  /** Draft-map key: the session id, a provisional `local_*` key, or null. */
  chatKey: string | null;
}

function isDraftKey(sid: string): boolean {
  return sid === "__new__" || sid.startsWith("local_");
}

export function useBoundChat(): BoundChat {
  const scoped = useOptionalScopedSessionId();
  const globalSessionId = useSessionStore((s) => s.currentSessionId);
  const globalChatKey = useSessionStore((s) => s.activeChatKey);
  if (scoped === null) {
    return { sessionId: globalSessionId, chatKey: globalChatKey };
  }
  return {
    sessionId: isDraftKey(scoped) ? null : scoped,
    chatKey: scoped === "__new__" ? null : scoped,
  };
}
