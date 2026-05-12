import React, { useEffect, useState, useRef } from 'react';
import { writeFileSync } from 'fs';
import { join } from 'path';
import { Box, Text, useApp, useInput } from '../runtime/index';
import type { ScrollBoxHandle } from '../runtime/index';
import { Shell, ModalHost, ToastHost } from '../ui/index.js';
import { StatsEnvelope, ConnectionState } from '../ws/client.js';
import { BottomBar } from '../components/BottomBar.js';
import { Messages } from '../components/Messages.js';
import { Spinner } from '../components/Spinner.js';
import { Turn } from '../components/Turn.js';
import { TranscriptViewport } from '../components/TranscriptViewport.js';
import { PromptInput } from '../components/PromptInput/PromptInput.js';
import { handleSlash } from '../commands/handler.js';
import { loadHistory, appendHistory } from '../utils/history.js';
import { copyToClipboard } from '../utils/clipboard.js';
import { useTheme } from '../theme/ThemeProvider.js';
import { isThemeSetting } from '../theme/themes.js';
import type {
  REPLProps,
  AgentInfo,
  Activity,
  BranchRow,
  ChannelActivity,
  PickerKind,
  PendingAttach,
  SessionAliasRow,
  ChannelAccountRow,
  PastConversation,
  RegisterForm,
  SearchResultRow,
  ThinkingEffort,
} from './repl/types.js';
import { ChannelActivityFeed } from '../components/ChannelActivityFeed.js';
import { randomLocalId, renderModel } from './repl/helpers.js';
import { buildPickerNode } from './repl/pickerRouter.js';
import { useWsEvents } from './repl/useWsEvents.js';

export type { REPLProps } from './repl/types.js';

export const REPL: React.FC<REPLProps> = ({ client, initialAgent, initialConversation }) => {
  const app = useApp();
  const [committed, setCommitted] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState<Turn | null>(null);
  const [agent, setAgent] = useState<string | undefined>(initialAgent);
  const [model, setModel] = useState<string | undefined>(undefined);
  const [conversationId, setConversationId] = useState<string | undefined>(initialConversation);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [stats, setStats] = useState<StatsEnvelope['data'] | undefined>(undefined);
  const [tick, setTick] = useState(0);
  // Per-conversation token + context-window tracking. We key by conv_id
  // so switching branches (resume / new / load_conversation) flips the
  // BottomBar indicator to that branch's own usage.
  const [tokensByConv, setTokensByConv] = useState<
    Record<string, { input?: number; output?: number }>
  >({});
  const [windowByConv, setWindowByConv] = useState<Record<string, number>>({});
  // Branch-level token stats from /api/sessions/{id}/tokens. Augments
  // the per-turn input/output already tracked via WS events with
  // cache_hit_rate / cache_read_total / source_mix for BottomBar pills.
  const [tokenStatsByConv, setTokenStatsByConv] = useState<
    Record<string, {
      current_tokens: number;
      context_window: number;
      cache_hit_rate: number;
      cache_read_total: number;
      source_mix: Record<string, number>;
    }>
  >({});
  const [history, setHistory] = useState<string[]>(() => loadHistory());
  const [conversationTitle, setConversationTitle] = useState<string | undefined>(undefined);
  const [promptDraft, setPromptDraft] = useState<string | undefined>(undefined);
  const [searchBaseDraft, setSearchBaseDraft] = useState('');
  const [contextSearchQuery, setContextSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResultRow[]>([]);
  const [sessionLiveByConv, setSessionLiveByConv] = useState<Record<string, boolean>>({});
  // Ambient activity buffer for inbound channel turns (wechat/telegram/...)
  // landing on conversations the TUI is not currently focused on.
  // Populated by useWsEvents; rendered above BottomBar.
  const [channelActivityByConv, setChannelActivityByConv] = useState<
    Record<string, ChannelActivity>
  >({});
  const [bellEnabled, setBellEnabled] = useState(true);
  const [modelsList, setModelsList] = useState<string[]>([]);
  const [branchesList, setBranchesList] = useState<BranchRow[]>([]);
  const [pastConversations, setPastConversations] = useState<
    Array<{
      id?: string;
      title?: string;
      created_at?: number;
      /** Channel name for channel-bound sessions ("wechat", "telegram", …). */
      source?: string;
      /** Display name for the bound peer (e.g. WeChat nickname). */
      peer_display?: string;
    }>
  >([]);
  const [pickerKind, setPickerKind] = useState<PickerKind>(null);
  const [pendingAttach, setPendingAttach] = useState<PendingAttach | null>(null);
  const [registerForm, setRegisterForm] = useState<{
    channel?: string;
    accountId?: string;
  }>({});
  const { setThemeSetting, currentTheme, colors } = useTheme();
  const [channelAccounts, setChannelAccounts] = useState<
    Array<{ channel?: string; account_id?: string; configured?: boolean }>
  >([]);
  const [chosenChannel, setChosenChannel] = useState<string | undefined>(undefined);
  // Channel-binding scratch state — held while the user walks
  // through the channel→account→action→peer picker chain.
  const [chosenAccount, setChosenAccount] = useState<string | undefined>(undefined);
  // QR login progress: the ASCII art for the current QR + a status
  // message ("scanned", "waiting", etc.). Cleared when the picker
  // closes.
  const [qrAscii, setQrAscii] = useState<string | undefined>(undefined);
  const [qrStatus, setQrStatus] = useState<string | undefined>(undefined);
  const [agentsList, setAgentsList] = useState<AgentInfo[]>([]);
  const [toolsOn, setToolsOn] = useState(true);
  // Permission cycle: auto-approve safe tools or bypass approval.
  const [permissionMode, setPermissionMode] = useState<'ask' | 'auto' | 'bypass'>('bypass');
  // Thinking effort cycle: off → minimal → low → medium → high → xhigh → off.
  const [thinkingEffort, setThinkingEffort] = useState<ThinkingEffort>('xhigh');
  const [connState, setConnState] = useState<ConnectionState>(client.getState());
  const agentSetRef = useRef(false);
  const transcriptScrollRef = useRef<ScrollBoxHandle | null>(null);
  // Theme switch: with hermes-ink every render is a full cell-grid
  // frame, so changing useColors() context just re-renders the entire
  // tree with the new palette — no Static remount or nonce needed.
  const lastThemeRef = useRef<string>(currentTheme);
  // Cache of the last list_session_aliases response. The /channel
  // picker reads this to render "you'll overwrite X" hints in option
  // descriptions without firing a fresh round-trip. Default empty so
  // the picker degrades to "no overwrite info" if alias data hasn't
  // landed yet.
  const sessionAliasesRef = useRef<SessionAliasRow[]>([]);
  // True ⇔ the next session_aliases envelope should be echoed to the
  // system area. /aliases sets this; picker pre-fetches do not. This
  // splits "load cache" from "show user the list" so both can use the
  // same WS action without one path dumping output into the other.
  const sessionAliasesPrintRef = useRef<boolean>(false);
  useEffect(() => {
    if (lastThemeRef.current !== currentTheme) {
      lastThemeRef.current = currentTheme;
    }
  }, [currentTheme]);

  // 1Hz tick for elapsed-seconds display while a turn is active.
  useEffect(() => {
    if (!activity) return;
    const t = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, [activity]);

  const pushSystem = (text: string) =>
    setCommitted((m) => [
      ...m,
      { id: `s-${Date.now()}-${m.length}`, role: 'system', text },
    ]);

  const startTurn = (verb: string) =>
    setActivity({ verb, startedAt: Date.now() });

  const finishTurn = () => setActivity(null);


  useWsEvents({
    client,
    pushSystem, finishTurn,
    bellEnabled, conversationId, chosenChannel, chosenAccount,
    setConversationId, setStreaming, setActivity, setCommitted,
    setTokensByConv, setWindowByConv, setTokenStatsByConv,
    setStats, setModel, setAgent, setAgentsList, setModelsList, setBranchesList,
    setChannelAccounts, setPastConversations,
    setQrAscii, setQrStatus,
    setPickerKind, setChosenChannel, setChosenAccount,
    setConversationTitle, setConnState,
    setToolsOn, setThinkingEffort, setPermissionMode,
    setSearchResults, setContextSearchQuery, setSessionLiveByConv,
    setChannelActivityByConv,
    agentSetRef, sessionAliasesPrintRef, sessionAliasesRef,
  });


  // Double-press Ctrl+C to exit (Claude Code / Hermes pattern).
  // First press: surface a "Press Ctrl+C again to exit" hint in
  // BottomBar and start an 800 ms timer. Second press inside the
  // window: app.exit(). Timer expires: clear the hint and reset.
  const [exitPending, setExitPending] = useState(false);
  const exitTimerRef = useRef<NodeJS.Timeout | null>(null);
  const lastCtrlCRef = useRef<number>(0);

  useEffect(() => () => {
    if (exitTimerRef.current) clearTimeout(exitTimerRef.current);
  }, []);

  useInput((input, key) => {
    if (key.ctrl && input === 'c') {
      const now = Date.now();
      const recent = now - lastCtrlCRef.current <= 800
        && exitTimerRef.current !== null;
      if (recent) {
        if (exitTimerRef.current) clearTimeout(exitTimerRef.current);
        exitTimerRef.current = null;
        setExitPending(false);
        app.exit();
        return;
      }
      lastCtrlCRef.current = now;
      setExitPending(true);
      if (exitTimerRef.current) clearTimeout(exitTimerRef.current);
      exitTimerRef.current = setTimeout(() => {
        exitTimerRef.current = null;
        setExitPending(false);
      }, 800);
      return;
    }
    // shift+tab cycles the modes that are usable without a separate
    // approval prompt UI.
    if (key.shift && key.tab) {
      setPermissionMode((m) => (m === 'bypass' ? 'auto' : 'bypass'));
      return;
    }
    // Esc closes the channel_qr_wait picker (no input form to absorb
    // it). Other pickers handle their own onCancel via Picker/LineInput
    // — this is just for the read-only QR display.
    if (key.escape && pickerKind === 'channel_qr_wait') {
      pushSystem('QR login cancelled.');
      setQrAscii(undefined);
      setQrStatus(undefined);
      setPickerKind(null);
      return;
    }
  });

  const onSubmit = (text: string) => {
    if (!text.trim()) return;
    // Save EVERY submitted line — chat messages and slash commands —
    // to up-arrow history. Previously only non-slash-handled inputs
    // landed in history; slash commands like `/channel` would
    // disappear after submit and ↑ wouldn't bring them back.
    setHistory((h) => {
      if (h[h.length - 1] === text) return h;
      appendHistory(text);
      return [...h, text].slice(-500);
    });
    if (text.startsWith('/')) {
      if (text.trim().startsWith('/search')) setSearchBaseDraft('');
      const handled = handleSlash(text, {
        client,
        pushSystem,
        clearCommitted: () => {
          setCommitted([]);
        },
        newSession: () => {
          setConversationId(undefined);
          setConversationTitle(undefined);
          setStreaming(null);
          setCommitted([]);
        },
        exit: () => app.exit(),
        openPicker: (kind) => setPickerKind(kind),
        toggleTools: () => setToolsOn((on) => !on),
        currentThinkingEffort: thinkingEffort,
        setThinkingEffort,
        toggleBell: () => {
          let next = bellEnabled;
          setBellEnabled((b) => {
            next = !b;
            return next;
          });
          return next;
        },
        showWelcome: () => {
          if (!stats) {
            pushSystem('Stats not loaded yet — try again in a moment.');
            return;
          }
          const lines = [
            `OpenProgram · ${stats.agent?.name ?? '—'} · ${stats.agent?.model ?? '—'}`,
            `${stats.programs_count ?? 0} programs · ${stats.skills_count ?? 0} skills · ${stats.agents_count ?? 0} agents · ${stats.conversations_count ?? 0} sessions`,
          ];
          if (stats.top_programs?.length) {
            lines.push(`programs: ${stats.top_programs.map((p) => p.name).filter(Boolean).join(' · ')}`);
          }
          if (stats.top_skills?.length) {
            lines.push(`skills: ${stats.top_skills.map((s) => s.name).filter(Boolean).join(' · ')}`);
          }
          pushSystem(lines.join('\n'));
        },
        showAgentInfo: () => {
          const a = agentsList.find((x) => x.id === agent);
          if (!a) {
            pushSystem('No active agent.');
            return;
          }
          const lines = [
            `agent: ${a.name ?? a.id}  (${a.id})`,
            `model: ${renderModel(a.model) ?? '—'}`,
            `default: ${a.default ? 'yes' : 'no'}`,
          ];
          pushSystem(lines.join('\n'));
        },
        lastAssistantText: () => {
          for (let i = committed.length - 1; i >= 0; i--) {
            if (committed[i]?.role === 'assistant') return committed[i]!.text;
          }
          return null;
        },
        copyToClipboard: copyToClipboard,
        exportTranscript: (filename) => {
          const fname = filename ?? `openprogram-${Date.now()}.md`;
          const path = fname.startsWith('/') ? fname : join(process.cwd(), fname);
          const lines: string[] = [
            `# OpenProgram session ${conversationId ?? '(unsaved)'}`,
            `agent: ${agent ?? '—'}`,
            `model: ${model ?? '—'}`,
            '',
          ];
          for (const t of committed) {
            lines.push(`## ${t.role}`);
            lines.push('');
            lines.push(t.text);
            lines.push('');
            for (const tc of t.tools ?? []) {
              lines.push(`- tool: \`${tc.tool}\` ${tc.input ? `· ${tc.input}` : ''}`);
            }
            if ((t.tools ?? []).length) lines.push('');
          }
          writeFileSync(path, lines.join('\n'));
          return path;
        },
        currentAgent: agent,
        currentModel: model,
        currentConversation: conversationId,
        setTheme: (name: string) => {
          if (!isThemeSetting(name)) return false;
          setThemeSetting(name);
          return true;
        },
        requestAliasesPrint: () => { sessionAliasesPrintRef.current = true; },
      });
      if (handled) return;
    }
    setCommitted((m) => [...m, { id: `u-${Date.now()}`, role: 'user', text }]);
    if (!conversationTitle && committed.length === 0) {
      // Mirror server-side behaviour: first user message becomes the title.
      setConversationTitle(text.slice(0, 50) + (text.length > 50 ? '…' : ''));
    }
    startTurn('Thinking');
    client.send({
      action: 'chat',
      conv_id: conversationId,
      agent_id: agent,
      text,
      tools: toolsOn,
      thinking_effort: thinkingEffort,
      permission_mode: permissionMode,
    } as never);
  };

  const onCancel = () => {
    if (!conversationId) return;
    client.send({ action: 'stop', conv_id: conversationId });
    setStreaming(null);
    finishTurn();
    pushSystem('Stopped.');
  };

  const elapsed = activity ? (Date.now() - activity.startedAt) / 1000 : undefined;
  void tick; // depend on tick so elapsed re-renders every second
  const streamRate = (() => {
    if (!activity?.streamStartedAt || !activity.streamedChars) return undefined;
    const dt = (Date.now() - activity.streamStartedAt) / 1000;
    if (dt <= 0.1) return undefined;
    return Math.round(activity.streamedChars / dt);
  })();
  const sessionStatus = !conversationId
    ? 'empty'
    : sessionLiveByConv[conversationId]
    ? 'active'
    : 'loaded';

  // Picker switch lives in pickerRouter.tsx — every legacy
  // picker (model / agent / channel chain / theme / resume / etc.)
  // wires the same set of REPL setters and the WS client, so we
  // bundle them through a single ctx object.
  const pickerNode = buildPickerNode({
    client, colors, pushSystem,
    pickerKind, pendingAttach,
    chosenChannel, chosenAccount, conversationId,
    modelsList, model, agentsList, channelAccounts, branchesList,
    registerForm, qrAscii, qrStatus, pastConversations,
    contextSearchQuery, searchResults, searchBaseDraft,
    thinkingEffort,
    setPickerKind, setPendingAttach,
    setChosenChannel, setChosenAccount, setConversationId, setAgent,
    setQrAscii, setQrStatus, setCommitted, setStreaming, setRegisterForm,
    setContextSearchQuery, setSearchResults, setPromptDraft,
    setThinkingEffort,
    sessionAliasesRef,
  });

  return (
    <Shell mouseTracking mode="alt">
      <TranscriptViewport stickyBottom scrollRef={transcriptScrollRef}>
        <Messages
          committed={committed}
          streaming={streaming}
          welcome={pickerNode ? undefined : (stats ?? {})}
          fillWelcome={committed.length === 0 && !streaming && !pickerNode}
        />
      </TranscriptViewport>
      {activity ? (
        <Spinner
          verb={activity.verb}
          detail={
            streamRate !== undefined
              ? `${streamRate} chars/s${activity.detail ? ` · ${activity.detail}` : ''}`
              : activity.detail
          }
          elapsed={elapsed}
        />
      ) : null}
      {/* New-kit modals win over the legacy pickerKind switch. As
          screens migrate to ModalProvider.push(), they show here.
          Legacy pickerNode (channel / resume / etc.) renders below
          when no modal is open and no kit modal is mounted. */}
      <ModalHost />
      {/* Toasts overlay any open modal — shown above PromptInput so
          they don't get clipped by the bottom-anchored chrome. */}
      <ToastHost />
      {pickerNode ? (
        pickerNode
      ) : (
        <PromptInput
          onSubmit={onSubmit}
          busy={!!activity}
          onCancel={onCancel}
          history={history}
          initialDraft={promptDraft}
          onDraftApplied={() => setPromptDraft(undefined)}
          onContextSearch={(draft) => {
            setSearchBaseDraft(draft);
            setContextSearchQuery('');
            setSearchResults([]);
            setPickerKind('context_search');
          }}
        />
      )}
      <ChannelActivityFeed
        activities={channelActivityByConv}
        currentConvId={conversationId}
      />
      <BottomBar
          agent={agent}
          model={model}
          conversationId={conversationId}
          conversationTitle={conversationTitle}
          busy={!!activity}
          tokens={conversationId ? tokensByConv[conversationId] : undefined}
          toolsOn={toolsOn}
          permissionMode={permissionMode}
          thinkingEffort={thinkingEffort}
          connState={connState}
          sessionStatus={sessionStatus}
          contextWindow={conversationId ? windowByConv[conversationId] : undefined}
          cacheHitRate={conversationId ? tokenStatsByConv[conversationId]?.cache_hit_rate : undefined}
          cacheReadTotal={conversationId ? tokenStatsByConv[conversationId]?.cache_read_total : undefined}
          tokenSourceMix={conversationId ? tokenStatsByConv[conversationId]?.source_mix : undefined}
          exitPending={exitPending}
        />
    </Shell>
  );
};
