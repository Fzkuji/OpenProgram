"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { wsRequest } from "@/lib/net/ws-request";
import { useSessionScope } from "@/lib/session-store/session-scope";

interface SandboxState {
  session_id?: string | null;
  sandbox?: boolean;
  sandbox_available?: boolean;
  sandbox_unavailable_reason?: string | null;
  error?: string;
}

export function useSandboxToggle(sessionId: string | null, persisted = true) {
  const sandbox = useSessionScope((s) => s.settings.sandbox !== false);
  const patchSettings = useSessionScope((s) => s.patchSettings);
  const [available, setAvailable] = useState(true);
  const [reason, setReason] = useState<string | null>(null);
  const operation = useRef<AbortController | null>(null);
  const busy = useRef(false);

  const apply = useCallback((d: SandboxState | null) => {
    if (!d || d.error || typeof d.sandbox !== "boolean") return false;
    setAvailable(d.sandbox_available !== false);
    setReason(d.sandbox_unavailable_reason ?? null);
    patchSettings({ sandbox: d.sandbox });
    return true;
  }, [patchSettings]);

  useEffect(() => {
    const controller = new AbortController();
    operation.current = controller;
    busy.current = false;
    setAvailable(true);
    setReason(null);
    // Provisional drafts already own a local choice; a server read has no row.
    if (sessionId && persisted) {
      void wsRequest<SandboxState>("set_sandbox", { session_id: sessionId },
        "sandbox_changed", { requestId: true, signal: controller.signal },
      ).then((d) => { if (!controller.signal.aborted) apply(d); });
    }
    return () => { controller.abort(); operation.current?.abort(); };
  }, [sessionId, persisted, apply]);

  const toggleSandbox = useCallback(() => {
    if (!available || busy.current) return;
    operation.current?.abort();
    const controller = new AbortController();
    operation.current = controller;
    busy.current = true;
    void wsRequest<SandboxState>("set_sandbox",
      { session_id: sessionId, sandbox_enabled: !sandbox }, "sandbox_changed",
      { requestId: true, signal: controller.signal },
    ).then((d) => {
      if (controller.signal.aborted) return;
      busy.current = false;
      if (!apply(d)) setReason("Could not update Sandbox. Reconnect and retry.");
    });
  }, [available, sandbox, sessionId, apply]);

  return { sandbox, sandboxAvailable: available, sandboxReason: reason, toggleSandbox };
}
