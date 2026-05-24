"use client";

/**
 * Compact chip strip listing files an assistant turn modified.
 *
 * Fires `list_turn_files` over the shared WS once the bubble is no
 * longer streaming; renders nothing when the turn touched no files
 * (the common case for chat-only replies, so empty state stays quiet).
 */
import { useEffect, useState } from "react";

import { useSessionStore } from "@/lib/session-store";

function wsSend(payload: unknown): boolean {
  const w = window as Window & { ws?: WebSocket };
  if (!w.ws || w.ws.readyState !== WebSocket.OPEN) return false;
  w.ws.send(JSON.stringify(payload));
  return true;
}

function basename(p: string): string {
  if (!p) return p;
  const i = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"));
  return i >= 0 ? p.slice(i + 1) : p;
}

export function TurnFilesChips({ assistantMsgId }: { assistantMsgId: string }) {
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [paths, setPaths] = useState<string[] | null>(null);

  useEffect(() => {
    if (!sessionId || !assistantMsgId) return;
    const w = window as Window & { ws?: WebSocket };
    const ws = w.ws;
    if (!ws) return;
    const onMsg = (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data);
        if (data?.type !== "list_turn_files_result") return;
        if (data?.data?.assistant_msg_id !== assistantMsgId) return;
        ws.removeEventListener("message", onMsg);
        setPaths(data?.data?.paths ?? []);
      } catch {
        /* ignore */
      }
    };
    ws.addEventListener("message", onMsg);
    const ok = wsSend({
      action: "list_turn_files",
      session_id: sessionId,
      assistant_msg_id: assistantMsgId,
    });
    if (!ok) ws.removeEventListener("message", onMsg);
    return () => {
      ws.removeEventListener("message", onMsg);
    };
  }, [sessionId, assistantMsgId]);

  if (!paths || paths.length === 0) return null;
  return (
    <div className="turn-files-chips">
      {paths.map((p) => (
        <span key={p} className="turn-files-chip" title={p}>
          {basename(p)}
        </span>
      ))}
    </div>
  );
}
