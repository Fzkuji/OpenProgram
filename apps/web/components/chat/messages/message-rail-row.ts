import type { TurnFileSummary } from "../../../lib/session-store/types.ts";
import type { FileWriteState } from "./turn-files-presentation.ts";

export type RailMsg = {
  id: string;
  content: string;
  preview: string;
  assistantId?: string;
  assistantSummary?: string;
  assistantTurnFiles?: TurnFileSummary;
  assistantFileWriteState?: FileWriteState;
  assistantReverted?: boolean;
};

const LEGACY_RAIL_SEP = "\u241f";

export function packRailRow(row: RailMsg): string {
  return JSON.stringify([
    row.id,
    row.content,
    row.preview,
    row.assistantId ?? "",
    row.assistantSummary ?? "",
    row.assistantTurnFiles ?? null,
    row.assistantFileWriteState ?? "none",
    Boolean(row.assistantReverted),
  ]);
}

export function unpackRailRow(packed: string): RailMsg {
  if (!packed.startsWith("[")) {
    const [id, content, preview, assistantId, assistantSummary] =
      packed.split(LEGACY_RAIL_SEP);
    return {
      id,
      content,
      preview,
      assistantId: assistantId || undefined,
      assistantSummary: assistantSummary || undefined,
      assistantFileWriteState: "none",
    };
  }
  const [
    id,
    content,
    preview,
    assistantId,
    assistantSummary,
    assistantTurnFiles,
    assistantFileWriteState,
    assistantReverted,
  ] = JSON.parse(packed);
  return {
    id,
    content,
    preview,
    assistantId: assistantId || undefined,
    assistantSummary: assistantSummary || undefined,
    assistantTurnFiles: assistantTurnFiles || undefined,
    assistantFileWriteState: assistantFileWriteState || "none",
    assistantReverted: Boolean(assistantReverted),
  };
}
