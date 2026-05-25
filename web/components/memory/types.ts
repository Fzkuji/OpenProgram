/**
 * Shared types for the Memory page.
 * Pulled out of memory-page.tsx so subcomponents can import
 * what they need without dragging the main page in.
 */

export interface WikiPage {
  path: string;
  title: string;
  type: string;
  size: number;
  mtime: number;
}

export interface JournalEntry {
  date: string;
  size: number;
  mtime: number;
}

export type Tab = "wiki" | "journal" | "core";

export interface EditorState {
  content: string;
  saving: boolean;
  saveStatus: "" | "saved" | "error";
  viewMode: "edit" | "preview";
}
