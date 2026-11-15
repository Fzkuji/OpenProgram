import type { AssistantBlock, TurnFileSummary } from "../../../../lib/session-store/types.ts";

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

export const TURN_FILES_COLLAPSE_AFTER = 3;
export const TURN_FILES_MAX_CARD_FILES = 20;

export type TurnFilesListLayout = {
  /** Header count; never smaller than the rows the card can list. */
  total: number;
  /** Rows rendered right now. */
  shown: number;
  /** Rows the "Show N more files" toggle reveals; 0 hides the toggle. */
  more: number;
  /** Whether the expanded list offers "Collapse". */
  collapse: boolean;
  /** Counted files the card does not list; > 0 renders the "open Review" row. */
  overflow: number;
};

/**
 * Row budget for the per-turn file card. The header count is the server's
 * file_count, which can exceed the rows the card holds (a bounded summary,
 * or more than MAX_CARD_FILES files). Every counted file stays reachable:
 * through the expand toggle, or through the overflow row that opens Review.
 */
export function turnFilesListLayout(
  loaded: number,
  fileCount: number,
  expanded: boolean,
  collapseAfter = TURN_FILES_COLLAPSE_AFTER,
  maxCardFiles = TURN_FILES_MAX_CARD_FILES,
): TurnFilesListLayout {
  const rows = Math.max(0, Math.min(loaded, maxCardFiles));
  const total = Math.max(fileCount, loaded);
  if (expanded) {
    return { total, shown: rows, more: 0, collapse: rows > collapseAfter, overflow: total - rows };
  }
  const shown = Math.min(rows, collapseAfter);
  const more = rows - shown;
  return { total, shown, more, collapse: false, overflow: more > 0 ? 0 : total - shown };
}

/** True when an embedded summary already holds every row the card can show. */
export function turnFilesSummaryComplete(loaded: number, fileCount: number): boolean {
  return loaded >= Math.min(fileCount, TURN_FILES_MAX_CARD_FILES);
}
