"use client";

/**
 * Component preview harness for the message-stream port.
 *
 * Two sections:
 *  - Static gallery — every bubble state rendered from fixtures.
 *  - Streaming replay — drives the real `applyChatWsMessage` reducer
 *    with a scripted WS sequence so the live streaming path (ack →
 *    thinking → tool → text → result) can be eyeballed without a
 *    backend. Verifies the L2 data layer end-to-end.
 *
 * Throwaway route — deleted once the phase-3 cutover lands.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { MessageList } from "@/components/chat/messages/message-list";
import { applyChatWsMessage } from "@/lib/chat-stream";
import { useSessionStore, type ChatMsg } from "@/lib/session-store";

const GALLERY_SID = "__preview_gallery__";
const STREAM_SID = "__preview_stream__";

const FIXTURES: ChatMsg[] = [
  {
    id: "u1",
    role: "user",
    content: "Summarise what changed in the auth module this week.",
    status: "done",
  },
  {
    id: "a1",
    role: "assistant",
    content:
      "Here's a quick rundown:\n\n- **Token rotation** is now automatic\n- The `login()` path validates `aud` claims\n- Added `requireRole()` middleware\n\n```ts\nrequireRole('admin')\n```",
    status: "done",
  },
  {
    id: "a2",
    role: "assistant",
    content: "Done — I checked the three files and they all line up.",
    thinking:
      "Let me trace the call path. login() → verifyToken() → decodeClaims().\nThe aud check was added in verifyToken, line 41. Looks consistent.",
    status: "done",
  },
  {
    id: "a3",
    role: "assistant",
    content: "I ran the tools and here is the result.",
    tools: [
      {
        id: "t1",
        tool: "read_file",
        input: '{"path": "src/auth/login.ts"}',
        result: "export function login() { /* ... 80 lines ... */ }",
        status: "done",
      },
      {
        id: "t2",
        tool: "grep",
        input: '{"pattern": "requireRole", "path": "src"}',
        result: "src/auth/mw.ts:12: export function requireRole(role) {",
        status: "done",
      },
      {
        id: "t3",
        tool: "run_tests",
        input: '{"suite": "auth"}',
        result: "FAILED: 1 of 24 — token_rotation_test",
        isError: true,
        status: "error",
      },
    ],
    status: "done",
  },
  { id: "a4", role: "assistant", content: "", status: "streaming" },
  { id: "a5", role: "assistant", content: "", status: "error" },
  {
    id: "u2",
    role: "user",
    content: "run analyze(target='auth', depth=2)",
    display: "runtime",
    status: "done",
  },
  {
    id: "r1",
    role: "assistant",
    content:
      "Analysis complete. 3 modules scanned, 1 warning:\n\n- `token_rotation_test` is flaky under concurrency.",
    display: "runtime",
    function: "analyze(target='auth', depth=2)",
    status: "done",
  },
];

/** A scripted WS sequence: `[delayMs, envelope]` pairs fed to
 *  `applyChatWsMessage` to mimic one live streaming turn. */
function streamScript(): Array<[number, unknown]> {
  const sid = STREAM_SID;
  const mid = "demo1";
  const se = (event: unknown) => ({
    type: "chat_response",
    data: { type: "stream_event", session_id: sid, msg_id: mid, event },
  });
  return [
    [
      0,
      {
        type: "chat_response",
        data: {
          type: "user_message",
          session_id: sid,
          msg_id: mid,
          content: "Trace the token-rotation bug and fix it.",
        },
      },
    ],
    [400, { type: "chat_ack", data: { session_id: sid, msg_id: mid } }],
    [500, se({ type: "thinking", text: "Looking at the rotation timer. " })],
    [400, se({ type: "thinking", text: "It resets on every request — " })],
    [400, se({ type: "thinking", text: "that's the leak." })],
    [
      500,
      se({
        type: "tool_use",
        tool: "read_file",
        input: '{"path": "src/auth/rotate.ts"}',
        tool_call_id: "tc1",
      }),
    ],
    [
      700,
      se({
        type: "tool_result",
        tool_call_id: "tc1",
        result: "let timer = setInterval(rotate, 60000)",
        is_error: false,
      }),
    ],
    [500, se({ type: "text", text: "Found it — the rotation timer " })],
    [350, se({ type: "text", text: "is re-armed on **every** request " })],
    [350, se({ type: "text", text: "instead of once.\n\n```ts\n" })],
    [350, se({ type: "text", text: "if (!timer) timer = setInterval(...)\n```" })],
    [
      500,
      {
        type: "chat_response",
        data: { type: "result", session_id: sid, msg_id: mid },
      },
    ],
  ];
}

export default function ChatPreviewPage() {
  const setMessages = useSessionStore((s) => s.setMessages);
  const [ready, setReady] = useState(false);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    setMessages(GALLERY_SID, FIXTURES);
    setMessages(STREAM_SID, []);
    setReady(true);
    return () => {
      for (const t of timers.current) clearTimeout(t);
    };
  }, [setMessages]);

  const replay = useCallback(() => {
    for (const t of timers.current) clearTimeout(t);
    timers.current = [];
    setMessages(STREAM_SID, []);
    let at = 0;
    for (const [delay, env] of streamScript()) {
      at += delay;
      timers.current.push(
        setTimeout(() => applyChatWsMessage(env as never), at),
      );
    }
  }, [setMessages]);

  const box: React.CSSProperties = {
    overflow: "auto",
    border: "1px solid var(--border)",
    borderRadius: 12,
    background: "var(--bg-primary)",
  };

  return (
    <div style={{ padding: "24px", maxWidth: 860, margin: "0 auto" }}>
      <h2 style={{ marginBottom: 6, color: "var(--text-bright)" }}>
        Streaming replay
      </h2>
      <p style={{ marginBottom: 10, color: "var(--text-muted)", fontSize: 13 }}>
        Drives the real WS reducer with a scripted sequence.
      </p>
      <button
        type="button"
        onClick={replay}
        style={{
          marginBottom: 12,
          padding: "6px 14px",
          borderRadius: 8,
          border: "1px solid var(--border)",
          background: "var(--bg-hover)",
          color: "var(--text-bright)",
          cursor: "pointer",
        }}
      >
        ▶ Replay streaming turn
      </button>
      <div style={{ ...box, height: "42vh", marginBottom: 28 }}>
        {ready ? <MessageList sessionId={STREAM_SID} /> : null}
      </div>

      <h2 style={{ marginBottom: 16, color: "var(--text-bright)" }}>
        Static gallery
      </h2>
      <div style={{ ...box, height: "60vh" }}>
        {ready ? <MessageList sessionId={GALLERY_SID} /> : null}
      </div>
    </div>
  );
}
