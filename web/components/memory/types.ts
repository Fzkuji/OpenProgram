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

/** One memory paragraph, as the derived recent view records it. */
export interface RecentEvent {
  block_id?: string;
  path?: string;
  content?: string;
  when?: string;
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
