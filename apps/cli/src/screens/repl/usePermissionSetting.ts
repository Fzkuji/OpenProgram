import { useCallback, useEffect, useRef } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { BackendClient } from '../../ws/client.js';
import type { PermissionMode } from './types.js';

/** Confirm session permission changes before changing the visible mode. */
export function usePermissionSetting(
  client: BackendClient, sessionId: string | undefined, mode: PermissionMode,
  setMode: Dispatch<SetStateAction<PermissionMode>>, report: (text: string) => void,
): Dispatch<SetStateAction<PermissionMode>> {
  const current = useRef(mode);
  current.current = mode;
  const reportRef = useRef(report);
  reportRef.current = report;
  const version = useRef<number | undefined>(undefined);
  const draft = useRef(false);
  const pending = useRef<{ id: string; timer: ReturnType<typeof setTimeout> } | null>(null);
  useEffect(() => {
    version.current = undefined;
    draft.current = false;
    if (!sessionId) return;
    const read = () => client.send({ action: 'set_permission', session_id: sessionId });
    const unsubscribe = client.on((event) => {
      if (event.type === 'chat_ack' && event.data.session_id === sessionId) read();
      if (event.type !== 'permission_changed' || event.data.session_id !== sessionId) return;
      const value = event.data;
      if (pending.current && pending.current.id === value.request_id) {
        clearTimeout(pending.current.timer);
        pending.current = null;
      }
      draft.current = value.error === 'session_not_found';
      if (typeof value.version === 'number' && value.mode && value.version >= (version.current ?? 0)) {
        version.current = value.version;
        setMode(value.mode);
      }
      if (value.error && !draft.current) reportRef.current(`Permission update: ${value.error}`);
    });
    const unsubscribeState = client.onState((state) => { if (state === 'connected') read(); });
    return () => {
      unsubscribe();
      unsubscribeState();
      if (pending.current) clearTimeout(pending.current.timer);
      pending.current = null;
    };
  }, [client, sessionId, setMode]);
  return useCallback((value: SetStateAction<PermissionMode>) => {
    const next = typeof value === 'function' ? value(current.current) : value;
    if (!sessionId || draft.current) { setMode(next); return; }
    if (pending.current) return;
    if (client.getState() !== 'connected' || version.current === undefined) {
      reportRef.current('Permission settings are unavailable. Reconnect or wait for session loading.');
      return;
    }
    const id = `permission-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const timer = setTimeout(() => {
      pending.current = null;
      reportRef.current('Permission update was not confirmed. Reconnect and retry.');
    }, 4000);
    timer.unref?.();
    pending.current = { id, timer };
    client.send({ action: 'set_permission', session_id: sessionId, mode: next,
      expected_version: version.current, request_id: id });
  }, [client, sessionId, setMode]);
}
