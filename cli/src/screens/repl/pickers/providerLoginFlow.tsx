/**
 * In-TUI login flow for login-mode "add account" — drives the shared worker
 * endpoints /api/providers/{id}/login/{start,poll,submit,cancel}, the same ones
 * the web <ProviderLogin> uses. Opens a URL / shows a device code / answers
 * prompts, polling until done, then hands the result back to the accounts
 * picker. This is what lets the TUI add an OAuth / device-code / import-from-CLI
 * account natively instead of punting to the web.
 *
 * Polling is a SELF-RESCHEDULING setTimeout (never setInterval): exactly one
 * request is ever in flight, and a late tick can't clobber a finished flow
 * (finishedRef guards it). The component cancels the backend session if it
 * unmounts mid-flow.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Box, Text, useInput } from '../../../runtime/index';
import { useColors } from '../../../theme/ThemeProvider.js';
import { usePanelWidth } from '../../../utils/useTerminalWidth.js';
import { openInBrowser } from '../../../utils/backend.js';
import {
  startLogin,
  pollLogin,
  submitLogin,
  cancelLogin,
} from '../../../utils/providerAccounts.js';

interface Prompt {
  message: string;
  secret?: boolean;
}

const printable = (input: string): string => input.replace(/[\u0000-\u001f\u007f]/g, '');
const dropLast = (value: string): string => Array.from(value).slice(0, -1).join('');

export function ProviderLoginFlow({
  providerId,
  profileId,
  method,
  label,
  onDone,
  onCancel,
}: {
  providerId: string;
  profileId: string;
  method: string;
  label: string;
  /** Called once the flow reaches a terminal state. */
  onDone: (result: { ok: boolean; message: string }) => void;
  /** Called when the user aborts before completion. */
  onCancel: () => void;
}): React.ReactElement {
  const colors = useColors();
  const width = usePanelWidth();

  const [lines, setLines] = useState<string[]>([]);
  const [prompt, setPrompt] = useState<Prompt | null>(null);
  const [value, setValue] = useState('');

  const sessionRef = useRef<string | null>(null);
  const cursorRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const finishedRef = useRef(false);
  const submittedRef = useRef<string | null>(null);

  const stop = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  useEffect(() => {
    let cancelled = false;

    const tick = async () => {
      const sid = sessionRef.current;
      if (!sid) return;
      try {
        const d = await pollLogin(providerId, sid, cursorRef.current);
        if (cancelled) return;
        cursorRef.current = Math.max(cursorRef.current, d.cursor ?? 0);
        for (const ev of d.events ?? []) {
          if (ev.type === 'open_url' && ev.url) {
            openInBrowser(ev.url);
            setLines((l) => [...l, `Opened ${ev.url}`]);
          } else if (ev.type === 'progress') {
            setLines((l) => [...l, String(ev.message ?? '')]);
          } else if (ev.type === 'code') {
            setLines((l) => [...l, `${ev.user_code ?? ''}  —  ${ev.verification_uri ?? ''}`]);
          }
        }
        if (d.waiting && d.prompt && d.prompt.message !== submittedRef.current) {
          setPrompt({ message: d.prompt.message, secret: d.prompt.secret });
        } else if (!d.waiting) {
          setPrompt(null);
          submittedRef.current = null;
        }
        if (d.done) {
          finishedRef.current = true;
          stop();
          onDone({
            ok: !!d.ok,
            message: d.ok
              ? `Signed in${d.name ? `: ${d.name}` : ''}.`
              : (d.error || 'Login failed.'),
          });
          return;
        }
        timerRef.current = setTimeout(tick, 1000);
      } catch {
        if (!cancelled) timerRef.current = setTimeout(tick, 1000); // transient
      }
    };

    void (async () => {
      setLines([`Starting ${label}…`]);
      const r = await startLogin(providerId, method, profileId);
      if (cancelled) return;
      if (r.error || !r.session) {
        finishedRef.current = true;
        onDone({ ok: false, message: r.error || 'Could not start the login.' });
        return;
      }
      sessionRef.current = r.session;
      timerRef.current = setTimeout(tick, 600);
    })();

    return () => {
      cancelled = true;
      stop();
      // Abandon the backend session if we leave before it finished.
      if (!finishedRef.current && sessionRef.current) {
        void cancelLogin(providerId, sessionRef.current).catch(() => {});
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useInput((input, key) => {
    if (key.escape) {
      finishedRef.current = true;
      stop();
      onCancel();
      return;
    }
    if (!prompt) return; // nothing to type into while just waiting
    if (key.return) {
      const v = value;
      submittedRef.current = prompt.message;
      setValue('');
      setPrompt(null);
      const sid = sessionRef.current;
      if (sid) void submitLogin(providerId, sid, v).catch(() => {});
      return;
    }
    if (key.backspace || key.delete) {
      setValue(dropLast);
      return;
    }
    if (input && !key.ctrl && !key.meta) {
      const t = printable(input);
      if (t) setValue((vv) => vv + t);
    }
  });

  const display = prompt?.secret ? '•'.repeat(Array.from(value).length) : value;

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={colors.primary}
      paddingX={1}
      marginBottom={1}
      width={width}
    >
      <Text bold color={colors.primary}>{label}</Text>
      {lines.map((l, i) => (
        <Text key={i} color={colors.muted}>{l}</Text>
      ))}
      {prompt ? (
        <>
          <Text color={colors.text}>{prompt.message}</Text>
          <Box>
            <Text color={colors.primary}>{'> '}</Text>
            <Text color={colors.text}>{display}</Text>
            <Text color={colors.primary}>█</Text>
          </Box>
          <Text color={colors.muted}>enter submit · esc cancel</Text>
        </>
      ) : (
        <Text color={colors.muted}>working… · esc cancel</Text>
      )}
    </Box>
  );
}
