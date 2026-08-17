"use client";

import { useEffect } from "react";

import { useSessionScope } from "@/lib/session-store/session-scope";

export function useUnattendedMode(
  sessionId: string | null,
  send: (payload: unknown) => boolean,
) {
  const unattended = useSessionScope((state) => state.settings.unattended);
  const patchSettings = useSessionScope((state) => state.patchSettings);

  const toggleUnattended = () => {
    const next = !unattended;
    patchSettings({ unattended: next });
    if (sessionId) {
      send({ action: "set_attended", session_id: sessionId, attended: !next });
    }
  };

  useEffect(() => {
    if (sessionId) {
      send({
        action: "set_attended",
        session_id: sessionId,
        attended: !unattended,
      });
    }
  }, [sessionId, unattended, send]);

  return { unattended, toggleUnattended };
}
