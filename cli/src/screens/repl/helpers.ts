import type { AgentInfo } from './types.js';

export const tsToDate = (ts?: number): string => {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString();
};

/**
 * Generate a 10-char hex id for a freshly minted local conversation.
 * Mirrors the server's ``"local_" + uuid().hex[:10]`` shape from
 * webui/server.py:_get_or_create_conversation, so a TUI-side mint
 * matches what the server would have generated.
 */
export const randomLocalId = (): string => {
  // Math.random() gives 52 bits — enough entropy for the 10-char
  // hex slice. crypto.randomUUID isn't available in older Node ESM
  // workers without a preamble.
  let out = '';
  while (out.length < 10) {
    out += Math.floor(Math.random() * 0x100000000).toString(16);
  }
  return out.slice(0, 10);
};

/**
 * Look up the session_id currently bound to (channel, account, peer)
 * in the cached alias list. Returns ``undefined`` when no row matches
 * — i.e. attach would be a fresh write, not an overwrite.
 *
 * The server returns rows verbatim from session_aliases.json, so
 * ``peer`` is the nested ``{kind, id}`` object, not a flat string.
 * Always go through this helper instead of inlining the match —
 * keeping the channel/account/peer matching in one place ensures
 * all three call sites (channel_account, channel_action,
 * peer_input) detect the same overwrite cases the server's _match
 * does.
 */
export const findExistingAlias = (
  aliases: Array<{
    channel?: string;
    account_id?: string;
    peer?: { kind?: string; id?: string } | string;
    agent_id?: string;
    session_id?: string;
    conversation_id?: string;
  }>,
  channel: string,
  account_id: string,
  peerId: string,
  peerKind: 'direct' | 'group' | 'channel' = 'direct',
): string | undefined => {
  for (const a of aliases) {
    if (a.channel !== channel) continue;
    if ((a.account_id ?? 'default') !== (account_id || 'default')) continue;
    const peer = typeof a.peer === 'object' && a.peer !== null
      ? { kind: a.peer.kind ?? 'direct', id: a.peer.id ?? '' }
      : { kind: 'direct', id: '' };
    if (peer.id !== peerId) continue;
    if (peer.kind !== peerKind) continue;
    return a.session_id ?? a.conversation_id;
  }
  return undefined;
};

export const renderModel = (m: AgentInfo['model']): string | undefined => {
  if (!m) return undefined;
  if (typeof m === 'string') return m;
  return m.id ?? m.provider;
};

/**
 * Some runtimes embed the provider as a prefix in ``runtime.model``
 * (e.g. Codex emits ``openai-codex:gpt-5.5``). Strip it for display
 * so the BottomBar only shows the bare model id.
 */
export const stripProviderPrefix = (m: string | undefined): string | undefined => {
  if (!m) return m;
  const idx = m.indexOf(':');
  if (idx <= 0) return m;
  return m.slice(idx + 1);
};
