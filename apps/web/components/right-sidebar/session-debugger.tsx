"use client";

import { useExecutionDebugger } from "@/lib/use-execution-debugger";
import { DebuggerPanel } from "./debugger-panel";

/** Remounted by session ID so requests, selections and drafts never cross chats. */
export function SessionDebugger({ sessionId, active, requestedExecutionId }: {
  sessionId: string | null;
  active: boolean;
  requestedExecutionId?: string | null;
}) {
  const state = useExecutionDebugger(active, sessionId, requestedExecutionId);
  return <DebuggerPanel
    key={state.selectedExecutionId || "empty"}
    {...state}
    sessionId={sessionId}
    onSelectExecution={state.selectExecution}
    onCommand={state.command}
    onRespondWait={state.respondWait}
    onCreateDraft={async (input) => { await state.createDraft(input); }}
    onUpdateDraft={state.updateDraft}
    onDraftAction={state.draftAction}
    onRefresh={state.refresh}
  />;
}
