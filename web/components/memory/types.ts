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
