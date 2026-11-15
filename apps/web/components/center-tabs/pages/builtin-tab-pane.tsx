"use client";

/**
 * BuiltinTabPane — the wide, full-center form of the two library pages
 * that used to live in the right sidebar: Bookmarks and Web history.
 * They are cross-session archives, not views of the current session,
 * so they belong in the center as their own tab (Chrome's
 * chrome://bookmarks / chrome://history), reachable from the main menu.
 *
 * Bookmarks follows the browser-manager structure: fixed search header,
 * folder-only navigation, and one content list for the selected folder.
 * Web history keeps the shared wide list frame below.
 */


import { type BuiltinPage } from "@/lib/tabs/center-tabs-store";
// Global row styles for the Bookmarks and History pages (plain class names,
// used only here); imported before the module CSS, as app/styles.css was.
import "./bookmarks.css";
import "./web-history.css";
import { BookmarksPage } from "./library/bookmarks-page";
import { HistoryPage } from "./library/history-page";
import { DownloadsPage } from "./library/downloads-page";
import { OfficeWindow } from "@/components/applications/windows/office-window";
import { RecordingWindow } from "@/components/applications/windows/recording-window";
import { MeetingWindow } from "@/components/applications/windows/meeting-window";

export function BuiltinTabPane({ page }: { page: BuiltinPage }) {
  if (page === "bookmarks") return <BookmarksPage />;
  if (page === "downloads") return <DownloadsPage />;
  if (page === "office") return <OfficeWindow />;
  if (page === "recording") return <RecordingWindow />;
  if (page === "meeting") return <MeetingWindow />;
  return <HistoryPage />;
}
