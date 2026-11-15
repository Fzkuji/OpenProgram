import { create } from "zustand";

export type DecisionAnswer =
  | { picked: Set<string>; custom: string }
  | { pick: "once" | "session" | "always" | "always_path" | "deny" | null }
  | { fields: Record<string, string | number | boolean> };

export interface DecisionDraft {
  answers: DecisionAnswer[];
  stepIndex: number;
  collapsed: boolean;
  discussionOpen: boolean;
  feedback: string;
  feedbackLocked: boolean;
}

// Presentation state only. Canonical answers and uncertain retry commands
// remain in the acknowledged submission store.
export const useDecisionDrafts = create<{
  drafts: Record<string, DecisionDraft>;
  setDraft: (key: string, draft: DecisionDraft) => void;
}>((set) => ({
  drafts: {},
  setDraft: (key, draft) => set(state => ({ drafts: { ...state.drafts, [key]: draft } })),
}));
