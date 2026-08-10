/**
 * Shared types for the Memory page.
 * Pulled out of memory-page.tsx so subcomponents can import
 * what they need without dragging the main page in.
 */

export interface TopicPage {
  path: string;
  title: string;
  type: string;
  size: number;
  mtime: number;
}

export interface TimelineDay {
  date: string;
  size: number;
  mtime: number;
}

/**
 * One memory paragraph, as the derived recent view records it.
 * Field names match what `rebuild_derived_views` writes into
 * `recent_events.jsonl` — tests/unit/test_memory_recent_contract.py
 * fails if either side renames one. `when` is null on a unit whose
 * date comes only from its evidence rows.
 */
export interface RecentEvent {
  memory_id: string;
  topic_path: string;
  content: string;
  when: string | null;
}

export interface WriterFailure {
  at: string;
  reason_code: string;
  retryable: boolean;
}

export interface WriterStatus {
  /** Which of the two records below the writer stamped most recently. */
  last_outcome: "success" | "failure" | null;
  last_success_at: string | null;
  last_failure: WriterFailure | null;
  /** Number of eligible user/assistant message nodes not yet written. */
  pending_turns: number | null;
}

/** Exact response contract of GET /api/memory/status. */
export interface MemoryStatus {
  workspace: string;
  revision: string;
  topic_files: number;
  blocks: number;
  source_files: number;
  timeline_files: number;
  recent_events: number;
  relations: number;
  core_exists: boolean;
  embedding_available: boolean;
  writer: WriterStatus;
}

/**
 * The four things memory holds. `topics` is what the model edits;
 * `timeline` and `recent` are derived from it; `core` is the block on
 * every system prompt.
 */
export type Tab = "topics" | "timeline" | "recent" | "core";

export interface EditorState {
  content: string;
  saving: boolean;
  saveStatus: "" | "saved" | "error";
  viewMode: "edit" | "preview";
}
