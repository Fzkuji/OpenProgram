import { create } from "zustand";
import type { PendingDecision } from "@/lib/session-store/types";
import type { WaitCommand } from "@/lib/net/execution/execution-client";

export interface DecisionSubmission {
  decision: PendingDecision;
  command: WaitCommand;
  status: "sending" | "unknown" | "answered" | "declined" | "rejected" | "closed";
  error?: string;
}

// Session UI receipts, not Workflow checkpoints. Keeping the command while
// navigating also prevents an uncertain delivery from acquiring a new ID.
export const useDecisionSubmissions = create<{
  submissions: Record<string, DecisionSubmission>;
  setSubmission: (id: string, submission: DecisionSubmission) => void;
}>((set) => ({
  submissions: {},
  setSubmission: (id, submission) => set(state => ({
    submissions: { ...state.submissions, [id]: submission },
  })),
}));
