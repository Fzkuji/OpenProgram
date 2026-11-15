export interface GoalVerification {
  id: string;
  status: "pending" | "met" | "unmet" | "unavailable";
  reason?: string;
  requirements?: { id: string; text?: string; verdict?: string; reason?: string; evidence?: string[] }[];
}

export function verificationSummary(
  value: GoalVerification, status: string | undefined,
  text: (en: string, zh: string) => string,
): string {
  if (value.status === "met") {
    return text("Goal completed", "目标已完成");
  }
  if (value.status === "unmet") return text("Goal verification failed", "目标验收未通过");
  if (status === "cancelled") return text("Verification cancelled", "验收已取消");
  if (status === "error" || status === "interrupted") return text("Verification interrupted", "验收已中断");
  return value.status === "pending"
    ? text("Verifying goal…", "正在验收目标…")
    : text("Verification result not confirmed", "验收结果未确认");
}
