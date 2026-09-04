import React, { useEffect, useState } from 'react';
import { Box, Text } from '../runtime/index';
import type { BackendClient, ConnectionState } from '../ws/client.js';
import { backendBase, backendFetch } from '../utils/backend.js';
import { usePanelWidth } from '../utils/useTerminalWidth.js';
import { useColors } from '../theme/ThemeProvider.js';

interface GoalSnapshot {
  version?: number;
  status: string;
  phase?: string;
  text?: string;
  checklist?: Array<{ done?: boolean }>;
  questions?: Array<{ status?: string }>;
}
interface GoalView {
  sessionId?: string;
  goal?: GoalSnapshot | null;
  connection: ConnectionState;
  loading: boolean;
  failed: boolean;
}
const terminal = new Set(['achieved', 'impossible', 'cancelled', 'cleared']);
const clean = (value: string) => value.replace(/\p{C}/gu, ' ');

function snapshot(value: unknown): GoalSnapshot | null | undefined {
  if (value === null) return null;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const g = value as GoalSnapshot;
  if (typeof g.status !== 'string') return undefined;
  if (g.version !== undefined && (!Number.isSafeInteger(g.version) || g.version < 0)) return undefined;
  return g;
}

/** Read-only projection: the worker remains the sole owner of Goal state. */
function useGoalView(client: BackendClient, sessionId?: string): GoalView {
  const [view, setView] = useState<GoalView>({ sessionId, connection: client.getState(), loading: !!sessionId, failed: false });
  useEffect(() => {
    let alive = true;
    let request = 0;
    let events = 0;
    let controller: AbortController | undefined;
    setView({ sessionId, connection: client.getState(), loading: !!sessionId, failed: false });
    if (!sessionId) return;
    const apply = (goal: GoalSnapshot | null) => setView((previous) => {
      if (!alive || previous.sessionId !== sessionId) return previous;
      if (goal && (previous.goal?.version ?? 0) > (goal.version ?? 0)) return previous;
      return { ...previous, goal, loading: false, failed: false };
    });
    const refresh = async () => {
      const current = ++request;
      const atStart = events;
      controller?.abort();
      const own = controller = new AbortController();
      setView((v) => ({ ...v, loading: true, failed: false }));
      try {
        const response = await backendFetch(`${backendBase()}/api/sessions/${encodeURIComponent(sessionId)}/goal`, {
          signal: AbortSignal.any([own.signal, AbortSignal.timeout(10000)]),
        });
        if (!response.ok && response.status !== 404) throw new Error('Goal read failed');
        const body = response.status === 404 ? null : await response.json() as { goal?: unknown } | null;
        const value = response.status === 404 ? null : snapshot(body?.goal);
        if (value === undefined) throw new Error('Invalid Goal snapshot');
        if (!alive || current !== request || own.signal.aborted) return;
        // An unversioned absence cannot erase a live update received in flight.
        if (value !== null || events === atStart) apply(value);
        setView((v) => ({ ...v, loading: false }));
      } catch {
        if (alive && current === request && !own.signal.aborted) {
          setView((v) => ({ ...v, loading: false, failed: true }));
        }
      }
    };
    const off = client.on((frame) => {
      const data = ('data' in frame ? frame.data : null) as Record<string, unknown> | null;
      if (!data) return;
      const loaded = frame.type === 'session_loaded' && data.id === sessionId;
      const update = (frame.type as string) === 'goal_update'
        || (frame.type === 'chat_response' && data.type === 'goal_update');
      if (!loaded && !(update && data.session_id === sessionId)) return;
      const goal = snapshot(data.goal);
      if (goal === undefined) return;
      events++;
      // Legacy/no-Goal hydration has no version; never retract a live Goal.
      if (goal === null) setView((v) => v.goal ? v : { ...v, goal: null, loading: false });
      else apply(goal);
    });
    const offState = client.onState((connection) => {
      setView((v) => ({ ...v, connection }));
      if (connection === 'connected') void refresh();
      else { controller?.abort(); setView((v) => ({ ...v, loading: false })); }
    });
    if (client.getState() === 'connected') void refresh();
    return () => { alive = false; controller?.abort(); off(); offState(); };
  }, [client, sessionId]);
  return view.sessionId === sessionId ? view : {
    sessionId, connection: client.getState(), loading: !!sessionId, failed: false,
  };
}

export function GoalStatus({ client, sessionId }: { client: BackendClient; sessionId?: string }) {
  const view = useGoalView(client, sessionId);
  const colors = useColors();
  const width = usePanelWidth();
  const g = view.goal;
  if (!sessionId || (g && terminal.has(g.status))) return null;
  if (!g && !view.failed && !view.loading && view.connection === 'connected') return null;
  const pending = Array.isArray(g?.questions) ? g.questions.filter((q) => q?.status === 'pending').length : 0;
  const checklist = Array.isArray(g?.checklist) ? g.checklist : [];
  const progress = checklist.length ? `${checklist.filter((item) => item?.done).length}/${checklist.length}` : '';
  const saved = g ? 'last confirmed' : 'no snapshot';
  const freshness = view.connection !== 'connected' ? `offline · ${saved}`
    : view.failed ? `refresh failed · ${saved}` : view.loading ? 'refreshing' : '';
  return <Box width={width} flexDirection="column" paddingX={1}>
    <Text color={view.failed || view.connection !== 'connected' ? colors.warning : colors.muted} wrap="truncate-end">
      {['Goal', g ? clean(g.status) : '', typeof g?.phase === 'string' ? clean(g.phase) : '', progress, pending ? `${pending} pending` : '', freshness].filter(Boolean).join(' · ')}
    </Text>
    <Text wrap="truncate-end">{typeof g?.text === 'string' ? clean(g.text) : 'Goal status unavailable'}</Text>
    <Text color={colors.muted}>Details and controls: /goal</Text>
  </Box>;
}
