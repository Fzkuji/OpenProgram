import type { AssistantBlock, TurnFileSummary } from "../../../lib/session-store/types.ts";

const FILE_WRITING_TOOLS = new Set(["write", "edit", "apply_patch"]);

export type FileWriteState = "none" | "attempted" | "failed";

function fileWritingBlocks(blocks?: AssistantBlock[]): AssistantBlock[] {
  return (blocks ?? []).filter(
    (block) => block.type === "tool"
      && FILE_WRITING_TOOLS.has((block.tool || "").toLowerCase()),
  );
}

export function fileWriteState(blocks?: AssistantBlock[]): FileWriteState {
  const writes = fileWritingBlocks(blocks);
  if (writes.length === 0) return "none";
  return writes.every((block) => block.is_error === true) ? "failed" : "attempted";
}

export function shouldRenderTurnFiles(
  summary?: TurnFileSummary,
  blocks?: AssistantBlock[],
  writeState = fileWriteState(blocks),
): boolean {
  return Boolean(summary) || writeState !== "none";
}

export function allFileWritesFailed(blocks?: AssistantBlock[]): boolean {
  return fileWriteState(blocks) === "failed";
}

export type LegacyTurnFilesLoadState = {
  status: "loading" | "loaded" | "error";
  attempt: number;
};

export type LegacyTurnFilesLoadEvent =
  | { type: "retry" }
  | { type: "resolved"; ok: boolean };

export const initialLegacyTurnFilesLoadState: LegacyTurnFilesLoadState = {
  status: "loading",
  attempt: 0,
};

export function legacyTurnFilesLoadReducer(
  state: LegacyTurnFilesLoadState,
  event: LegacyTurnFilesLoadEvent,
): LegacyTurnFilesLoadState {
  if (event.type === "retry") {
    return { status: "loading", attempt: state.attempt + 1 };
  }
  return { ...state, status: event.ok ? "loaded" : "error" };
}
