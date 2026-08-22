"use client";

import { useCallback, useEffect, useState } from "react";

import { wsRequest } from "@/lib/net/ws-request";
import { useSessionScope } from "@/lib/session-store/session-scope";

interface SandboxState {
  session_id?: string | null;
  sandbox?: boolean;
  sandbox_enabled?: boolean | null;
  sandbox_available?: boolean;
  sandbox_unavailable_reason?: string | null;
}

export function useSandboxToggle(sessionId: string | null) {
  const sandbox = useSessionScope((s) => s.settings.sandbox !== false);
  const patchSettings = useSessionScope((s) => s.patchSettings);
  const [available, setAvailable] = useState(true);
  const [reason, setReason] = useState<string | null>(null);

  const apply = useCallback((d: SandboxState | null) => {
    if (!d) return;
    setAvailable(d.sandbox_available !== false);
    setReason(d.sandbox_unavailable_reason ?? null);
    if (typeof d.sandbox === "boolean") patchSettings({ sandbox: d.sandbox });
  }, [patchSettings]);

  useEffect(() => {
    // No session yet: a read returns inherit-ON and would overwrite a
    // local off before the first message creates the row.
    if (!sessionId) return;
    void wsRequest<SandboxState>(
      "set_sandbox",
      { session_id: sessionId },
      "sandbox_changed",
      (d) => !d.session_id || d.session_id === sessionId,
    ).then(apply);
  }, [sessionId, apply]);

  const toggleSandbox = useCallback(() => {
    if (!available) return;
    const next = !sandbox;
    patchSettings({ sandbox: next });
    void wsRequest<SandboxState>(
      "set_sandbox",
      { session_id: sessionId, sandbox_enabled: next },
      "sandbox_changed",
    ).then(apply);
  }, [available, sandbox, sessionId, patchSettings, apply]);

  return { sandbox, sandboxAvailable: available, sandboxReason: reason, toggleSandbox };
}
