"use client";

/**
 * AttachmentPreview — open one chat attachment, whichever kind it is.
 *
 * The body is the existing `FileViewer` in absolute-path mode, so an
 * attachment gets exactly the renderers a project file gets: image, PDF
 * in the browser's own viewer, rendered markdown, line-numbered code,
 * and a name+size+download card for anything binary. Nothing here
 * decides how a file looks — that judgement stays in one place.
 *
 * An overlay rather than a center file tab: a chat attachment is
 * usually a glance (does this screenshot show what I meant?), and an
 * overlay keeps the reading position in the transcript. Files worth
 * living with belong in the project tree, which already has tabs.
 */
import { createPortal } from "react-dom";

import { FileViewer } from "@/components/files/file-viewer";
import { useTranslation } from "@/lib/i18n";
import { useModalA11y } from "@/lib/use-modal-a11y";
import { useSessionStore } from "@/lib/session-store";
import { absRawFileUrl } from "@/lib/state/files-shared";

export function AttachmentPreview({
  path,
  filename,
  onClose,
}: {
  path: string;
  filename: string;
  onClose: () => void;
}) {
  const { text } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);

  // Replaces the old window-level Escape listener: same Escape, plus
  // the Tab trap and focus-return to the chip that opened the preview.
  const modal = useModalA11y(onClose, filename);

  if (typeof document === "undefined") return null;
  return createPortal(
    <div
      className="attach-preview-backdrop"
      data-native-view-occluder="true"
      onClick={onClose}
    >
      <div
        {...modal}
        className="attach-preview-panel"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="attach-preview-head">
          <span className="attach-preview-name" title={path}>
            {filename}
          </span>
          <a
            className="attach-preview-download"
            href={absRawFileUrl(path, sessionId ?? undefined)}
            download={filename}
            onClick={(e) => e.stopPropagation()}
          >
            {text("Download", "下载")}
          </a>
          <button
            type="button"
            className="attach-preview-close"
            onClick={onClose}
            aria-label={text("Close preview", "关闭预览")}
          >
            ×
          </button>
        </div>
        <div className="attach-preview-body">
          <FileViewer
            projectId=""
            path={path}
            abs
            sessionId={sessionId ?? undefined}
          />
        </div>
      </div>
    </div>,
    document.body,
  );
}
