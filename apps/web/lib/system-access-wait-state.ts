export type SystemAccessWait = {
  wait_id: string;
  session_id: string;
  execution_id?: string;
  required_capabilities: string[];
  [key: string]: unknown;
};

/**
 * The WebSocket owner can receive a live wait before the session transcript
 * mounts. Keep the wait projection outside React so the visible session can
 * adopt it on mount without turning a history/reconnect replay into a native
 * setup request.
 */
const openBySession = new Map<string, Map<string, SystemAccessWait>>();
const handledBySession = new Map<string, Set<string>>();
const liveBySession = new Map<string, Map<string, SystemAccessWait>>();

function put(
  map: Map<string, Map<string, SystemAccessWait>>,
  wait: SystemAccessWait,
): void {
  let session = map.get(wait.session_id);
  if (!session) {
    session = new Map();
    map.set(wait.session_id, session);
  }
  session.set(wait.wait_id, wait);
}

export function rememberSystemAccessWait(
  value: unknown,
  live = false,
): SystemAccessWait | null {
  if (!value || typeof value !== "object") return null;
  const wait = value as Partial<SystemAccessWait>;
  if (typeof wait.wait_id !== "string" || typeof wait.session_id !== "string") return null;
  if (!Array.isArray(wait.required_capabilities)) return null;
  const normalized: SystemAccessWait = {
    ...wait,
    wait_id: wait.wait_id,
    session_id: wait.session_id,
    required_capabilities: wait.required_capabilities.filter(
      (id): id is string => typeof id === "string",
    ),
  };
  put(openBySession, normalized);
  if (live && !systemAccessWaitHandled(normalized)) put(liveBySession, normalized);
  return normalized;
}

export function forgetSystemAccessWait(value: unknown): void {
  if (!value || typeof value !== "object") return;
  const wait = value as Partial<SystemAccessWait>;
  if (typeof wait.wait_id !== "string" || typeof wait.session_id !== "string") return;
  const handled = handledBySession.get(wait.session_id);
  handled?.delete(wait.wait_id);
  if (handled?.size === 0) handledBySession.delete(wait.session_id);
  for (const map of [openBySession, liveBySession]) {
    const session = map.get(wait.session_id);
    session?.delete(wait.wait_id);
    if (session?.size === 0) map.delete(wait.session_id);
  }
}

export function rememberedSystemAccessWaits(sessionId: string): SystemAccessWait[] {
  return [...(openBySession.get(sessionId)?.values() || [])];
}

export function takeLiveSystemAccessWaits(sessionId: string): SystemAccessWait[] {
  const session = liveBySession.get(sessionId);
  if (!session) return [];
  liveBySession.delete(sessionId);
  return [...session.values()];
}

export function systemAccessWaitHandled(wait: SystemAccessWait): boolean {
  return handledBySession.get(wait.session_id)?.has(wait.wait_id) ?? false;
}

export function markSystemAccessWaitHandled(value: unknown): void {
  if (!value || typeof value !== "object") return;
  const wait = value as Partial<SystemAccessWait>;
  if (typeof wait.wait_id !== "string" || typeof wait.session_id !== "string") return;
  let handled = handledBySession.get(wait.session_id);
  if (!handled) { handled = new Set(); handledBySession.set(wait.session_id, handled); }
  handled.add(wait.wait_id);
  const session = liveBySession.get(wait.session_id);
  session?.delete(wait.wait_id);
  if (session?.size === 0) liveBySession.delete(wait.session_id);
}
