/** Store keys of sub-agent transcripts, kept apart from their loader so the
 *  session store can drop a removed session's transcripts too. */

const SUB_AGENT_KEY = "#sub-agent:";

/** The store key holding one sub-agent's turns, keyed by its first Job
 *  (older spawns without a Job id use their branch head). */
export function subAgentChatKey(sessionId: string, agentKey: string): string {
  return `${sessionId}${SUB_AGENT_KEY}${agentKey}`;
}

/** Whether a store chat key names a sub-agent transcript, of any session. */
export function isSubAgentChatKey(key: string): boolean {
  return key.indexOf(SUB_AGENT_KEY) > 0;
}

/** Whether a store chat key holds one of this session's sub-agent transcripts. */
export function ownsSubAgentChatKey(sessionId: string, key: string): boolean {
  return key.startsWith(`${sessionId}${SUB_AGENT_KEY}`);
}

/** The session a chat key belongs to: itself, or the session of the
 *  sub-agent transcript it names. */
export function chatKeySession(key: string): string {
  const marker = key.indexOf(SUB_AGENT_KEY);
  return marker > 0 ? key.slice(0, marker) : key;
}
