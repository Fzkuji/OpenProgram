"use client";

/** Shared project, attachment and retained-version preview dispatch.
 * FileViewer owns the authorized source URL. Format adapters only consume that
 * source and never resolve project/session paths themselves. Large decoders
 * load when their format is opened, independently of chat startup.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { lazy, Suspense } from "react";
import { fileCapabilities, fileExtension, IMAGE_EXTENSIONS } from "@/lib/documents/file-formats";
import { ImagePreview } from "./preview/image-preview";
import { MediaPreview } from "./preview/media-preview";
import { Download } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws-request";
import { Markdown } from "@/lib/format-utils/markdown";
import {
  absFileReadUrl,
  absRawFileUrl,
  cacheFileRead,
  type FileReadResult,
  fileResponseMatchesOwner,
  getCachedFileRead,
  latestFileMtime,
  rawFileUrl,
} from "@/lib/files/files-shared";
import styles from "./files-panel.module.css";
import previewStyles from "./preview/preview.module.css";

export const IMAGE_EXTS = IMAGE_EXTENSIONS;
const PdfPreview = lazy(() => import("./preview/pdf-preview").then((module) => ({ default: module.PdfPreview })));
const LayeredImagePreview = lazy(() => import("./preview/layered-image-preview").then((module) => ({ default: module.LayeredImagePreview })));

function fmtSize(bytes: number): string {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function FileViewer({
  projectId,
  path,
  abs = false,
  sessionId,
  mdRendered = true,
  draft,
  onDraftChange,
  onLoaded,
  snapshot,
  sourceBlob,
}: {
  projectId: string;
  path: string;
  /** ``path`` is an absolute worker-side path (a chat attachment), read
   *  through the attachment-root-checked HTTP routes instead of the
   *  project-scoped WS action. Never editable in this mode. */
  abs?: boolean;
  /** Scopes the attachment-root check to this session's bound project. */
  sessionId?: string;
  /** Markdown files: true → <Markdown> render, false → source lines.
   *  The toggle UI lives in the file tab's toolbar. */
  mdRendered?: boolean;
  /** The pane's editor buffer. When set (together with onDraftChange)
   *  the text body renders as the editable gutter+textarea and — for
   *  markdown in Rendered mode — the preview renders the draft, so an
   *  unsaved buffer never shows stale disk content. */
  draft?: string;
  onDraftChange?: (value: string) => void;
  /** Text files: fires when the read lands (null on failure) so the
   *  tab pane can seed its editor buffer / tell text from binary. */
  onLoaded?: (data: FileReadResult | null) => void;
  snapshot?: FileReadResult | null;
  sourceBlob?: Blob | null;
}) {
  const ext = fileExtension(path);
  const capability = fileCapabilities(path);
  const { text } = useTranslation();
  // Version endpoints return opaque bytes. SVG image decoding requires its
  // media type; rendering remains inside img, never inline document markup.
  const blobUrl = useMemo(() => sourceBlob ? URL.createObjectURL(ext === "svg"
    ? sourceBlob.slice(0, sourceBlob.size, "image/svg+xml") : sourceBlob) : null, [sourceBlob, ext]);
  useEffect(() => () => { if (blobUrl) URL.revokeObjectURL(blobUrl); }, [blobUrl]);
  const rawUrl = abs
    ? absRawFileUrl(path, sessionId)
    : rawFileUrl(projectId, path);
  const sourceUrl = blobUrl ?? rawUrl;
  if (capability.preview === "image") return <div className={previewStyles.preview}><ImagePreview key={sourceUrl} sourceUrl={sourceUrl} path={path} /></div>;
  if (capability.preview === "pdf" || capability.preview === "layered-image") {
    return <div className={previewStyles.preview}><Suspense fallback={<div>{text("Loading…", "加载中…")}</div>}>
      {capability.preview === "pdf" ? <PdfPreview key={sourceUrl} sourceUrl={sourceUrl} path={path} />
        : <LayeredImagePreview key={sourceUrl} sourceUrl={sourceUrl} path={path} />}
    </Suspense></div>;
  }
  if (capability.preview === "audio" || capability.preview === "video") {
    return <div className={previewStyles.preview}><MediaPreview key={sourceUrl + capability.preview} sourceUrl={sourceUrl} path={path} kind={capability.preview} /></div>;
  }
  return (
    <TextViewer
      projectId={projectId}
      path={path}
      abs={abs}
      sessionId={sessionId}
      isMarkdown={ext === "md"}
      rendered={mdRendered}
      draft={draft}
      onDraftChange={onDraftChange}
      onLoaded={onLoaded}
      snapshot={snapshot}
      downloadUrl={blobUrl ?? rawUrl}
    />
  );
}

function TextViewer({
  projectId,
  path,
  abs,
  sessionId,
  isMarkdown,
  rendered,
  draft,
  onDraftChange,
  onLoaded,
  snapshot,
  downloadUrl,
}: {
  downloadUrl?: string;
  projectId: string;
  path: string;
  abs?: boolean;
  sessionId?: string;
  isMarkdown: boolean;
  rendered: boolean;
  draft?: string;
  onDraftChange?: (value: string) => void;
  onLoaded?: (data: FileReadResult | null) => void;
  snapshot?: FileReadResult | null;
}) {
  const { text } = useTranslation();
  const [data, setData] = useState<FileReadResult | null>(null);
  const [failed, setFailed] = useState(false);
  // Ref so a changing callback identity never re-triggers the fetch.
  const onLoadedRef = useRef(onLoaded);
  onLoadedRef.current = onLoaded;

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setFailed(false);
    if (snapshot) {
      setData(snapshot);
      onLoadedRef.current?.(snapshot);
      return;
    }
    if (abs) {
      // No readCache / mtime here: an attachment is immutable in
      // practice and has no tree listing reporting an mtime, so a cache
      // would only add a staleness question nobody can answer.
      setData(null);
      fetch(absFileReadUrl(path, sessionId))
        .then((r) => (r.ok ? r.json() : null))
        .then((res: FileReadResult | null) => {
          if (cancelled) return;
          if (!res) {
            setFailed(true);
            onLoadedRef.current?.(null);
            return;
          }
          setData(res);
          onLoadedRef.current?.(res);
        })
        .catch(() => {
          if (cancelled) return;
          setFailed(true);
          onLoadedRef.current?.(null);
        });
      return () => {
        cancelled = true;
      };
    }
    const knownMtime = latestFileMtime.get(`${projectId}:${path}`);
    const hit = getCachedFileRead(projectId, path, knownMtime);
    if (hit) {
      setData(hit);
      onLoadedRef.current?.(hit);
      return;
    }
    setData(null);
    wsRequest<FileReadResult>(
      "project_file_read",
      { project_id: projectId, path },
      "project_file_read_result",
      (data) => fileResponseMatchesOwner(data as unknown as Record<string, unknown>, {
        project_id: projectId,
        path,
      }),
      4000,
      { signal: controller.signal },
    ).then((res) => {
      if (cancelled) return;
      if (!res || res.project_id !== projectId || res.error || res.path !== path) {
        setFailed(true);
        onLoadedRef.current?.(null);
        return;
      }
      cacheFileRead(res);
      setData(res);
      onLoadedRef.current?.(res);
    });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [projectId, path, abs, sessionId, snapshot]);

  if (failed) {
    return (
      <div className={styles.viewerHint}>
        {text("Couldn't read this file.", "无法读取该文件。")}
      </div>
    );
  }
  if (!data) {
    return <div className={styles.viewerHint}>{text("Loading…", "加载中…")}</div>;
  }

  if (data.binary || data.too_large || data.content === undefined) {
    return (
      <div className={styles.viewerHint}>
        <div className={styles.binaryCard}>
          <div className={styles.binaryName}>{path.split("/").pop()}</div>
          <div className={styles.binaryMeta}>
            {fmtSize(data.size)}
            {data.binary
              ? ` · ${text("binary file", "二进制文件")}`
              : data.too_large
                ? ` · ${text("too large to preview (>1 MB)", "过大，无法预览（>1 MB）")}`
                : ""}
          </div>
          <a
            className={styles.downloadLink}
            href={downloadUrl ?? (abs ? absRawFileUrl(path, sessionId) : rawFileUrl(projectId, path))}
            download={path.split("/").pop()}
          >
            <Download size={13} />
            {text("Download", "下载")}
          </a>
        </div>
      </div>
    );
  }

  if (isMarkdown && rendered) {
    return (
      <div className={styles.viewerScroll}>
        {/* message-content 复用 chat.css 的 markdown 元素样式（标题/列表/表格） */}
        <div className={`${styles.mdBody} message-content`}>
          <Markdown source={draft ?? data.content} escapeRawHtml />
        </div>
      </div>
    );
  }

  // Default for text files: the editable buffer. Falls back to the
  // read-only listing for truncated reads (saving a 1 MB-cut buffer
  // would destroy the tail) and for the one frame before the pane
  // seeds its draft from this read.
  if (draft !== undefined && onDraftChange) {
    return <EditorArea value={draft} onChange={onDraftChange} />;
  }

  return (
    <div className={styles.viewerScroll}>
      {data.truncated ? (
        <div className={styles.truncatedNote}>
          {text("Truncated at 1 MB", "已在 1 MB 处截断")}
        </div>
      ) : null}
      <NumberedCode content={data.content} />
    </div>
  );
}

/**
 * EditorArea — plain-text editor: line-number gutter + <textarea>,
 * same mono metrics on both sides, gutter scroll mirrored from the
 * textarea. wrap="off" so long lines scroll horizontally like a code
 * editor.
 * ponytail: no syntax highlight, whole-buffer re-render per keystroke;
 * swap for CodeMirror when highlighting or huge files measurably hurt.
 */
export function EditorArea({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const gutterRef = useRef<HTMLDivElement>(null);
  // Trailing "\n" opens a real (editable) empty last line in a
  // textarea, so plain split length — unlike NumberedCode — is right.
  const lineCount = value.split("\n").length;
  const gutter = useMemo(
    () => Array.from({ length: lineCount }, (_, i) => i + 1).join("\n"),
    [lineCount],
  );
  return (
    <div className={styles.editorWrap}>
      <div ref={gutterRef} className={styles.editorGutter} aria-hidden="true">
        {gutter}
      </div>
      <textarea
        className={styles.editorTextarea}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onScroll={(e) => {
          if (gutterRef.current)
            gutterRef.current.scrollTop = e.currentTarget.scrollTop;
        }}
        wrap="off"
        spellCheck={false}
      />
    </div>
  );
}

function NumberedCode({ content }: { content: string }) {
  // Split once per content change; trailing newline would add a phantom
  // empty last line, drop it.
  const lines = content.split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return (
    <pre className={styles.code}>
      {lines.map((l, i) => (
        <div key={i} className={styles.codeLine}>
          <span className={styles.lineNo}>{i + 1}</span>
          <span className={styles.lineText}>{l || " "}</span>
        </div>
      ))}
    </pre>
  );
}
