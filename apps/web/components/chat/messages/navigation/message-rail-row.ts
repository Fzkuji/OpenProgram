import type { TurnFileSummary } from "../../../../lib/session-store/types.ts";
import type { FileWriteState } from "../turn-files/turn-files-presentation.ts";

/** A task the turn's reply worked on: an entry of the rail's preview. */
export type RailTask = {
  id: string;
  title: string;
  /** The reply holding the task's block. */
  anchor: string;
  /** Step count, or the steps this reply covered when the task spans several. */
  steps?: number;
  from?: number;
  to?: number;
};

export type RailMsg = {
  id: string;
  content: string;
  preview: string;
  assistantId?: string;
  assistantSummary?: string;
  assistantTurnFiles?: TurnFileSummary;
  assistantFileWriteState?: FileWriteState;
  assistantReverted?: boolean;
  /** The turn is pinned as a chapter: its tick takes the theme accent. */
  chapter?: boolean;
  /** The tasks of the turn, in order; absent when it declared none. */
  tasks?: RailTask[];
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
    Boolean(row.chapter),
    row.tasks?.length ? row.tasks : null,
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
    chapter,
    tasks,
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
    chapter: Boolean(chapter),
    ...(Array.isArray(tasks) && tasks.length ? { tasks } : {}),
  };
}
