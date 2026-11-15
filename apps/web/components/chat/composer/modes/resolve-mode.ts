/** User decisions render in the transcript, independently of composer mode. */
import type { AgenticFunction } from "@/lib/session-store";

export type ComposerMode = "idle" | "fn-form";

export function resolveComposerMode(
  fnFormFunction: AgenticFunction | null,
): ComposerMode {
  return fnFormFunction ? "fn-form" : "idle";
}
