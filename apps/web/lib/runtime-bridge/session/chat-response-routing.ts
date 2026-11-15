interface SessionScopedResponse {
  session_id?: unknown;
}

/** Resolve response ownership from the wire envelope, falling back only for
 * legacy unscoped messages. */
export function responseSessionId(
  data: SessionScopedResponse | null | undefined,
  activeSessionId: string | null | undefined,
): string | null {
  const sessionId = data?.session_id;
  return typeof sessionId === "string" && sessionId
    ? sessionId
    : (activeSessionId ?? null);
}

/** Visible controls may only react to their active session. */
export function responseTargetsActiveChat(
  data: SessionScopedResponse | null | undefined,
  activeSessionId: string | null | undefined,
): boolean {
  const sessionId = data?.session_id;
  return typeof sessionId !== "string" || !sessionId || sessionId === activeSessionId;
}
