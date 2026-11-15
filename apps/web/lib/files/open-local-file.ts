import { previewTarget } from "@/lib/chat/transcript/file-outputs";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { canBrowseLocalFiles, isHtmlPath } from "./local-file-url";
import { openHtmlPage } from "./open-html-page";

/** Open a chat's file in the preview beside it: a web page as a page, and
 *  everything else in the file preview (an office file in the document
 *  editor, a PDF in the PDF viewer). A file inside the chat's project
 *  opens as that project's file, anything else read-only by its path. */
export function openLocalFile(path: string, sessionId: string, project?: { id: string; path: string } | null, automatic = false): boolean {
  if (isHtmlPath(path) && path.startsWith("/") && canBrowseLocalFiles()) return openHtmlPage(path, sessionId, automatic);
  return useCenterTabs.getState().openFilePreview(sessionId, previewTarget(path, project), automatic);
}
