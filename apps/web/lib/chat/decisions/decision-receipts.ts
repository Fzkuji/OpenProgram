import type { PendingDecision } from "@/lib/session-store/types";
import type { DecisionSubmission } from "./decision-submissions";

type Settlement = Pick<DecisionSubmission, "status" | "error"> | undefined;

export function isSettled(submission: Settlement): boolean {
  const status = submission?.status;
  return status === "answered" || status === "declined" || status === "closed";
}

/** Whether a settled decision keeps a card in the transcript. A settled
 *  permission leaves none: the tool row it gated already shows what ran or
 *  was refused. A closed or failed one still needs a look. */
export function showsReceipt(
  decision: Pick<PendingDecision, "kind">,
  submission: Settlement,
): boolean {
  if (!isSettled(submission)) return false;
  const decided = submission?.status === "answered" || submission?.status === "declined";
  return !(decision.kind === "approval" && decided && !submission?.error);
}
