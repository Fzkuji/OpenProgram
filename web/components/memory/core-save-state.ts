export type CoreSaveStatus = "" | "saved" | "error";

export function coreSaveStatus(
  currentContent: string,
  submittedContent: string,
  requestSucceeded: boolean,
): CoreSaveStatus {
  if (!requestSucceeded) return "error";
  return currentContent === submittedContent ? "saved" : "";
}
