"use client";

/**
 * FileViewer — body of a center file tab. Dispatches on the file's
 * extension:
 *
 *   images   → <img> straight off the backend raw endpoint
 *   pdf      → <iframe> off the raw endpoint (renders in the browser's
 *              built-in PDF viewer)
 *   markdown → Rendered (existing <Markdown>, marked-based) or Source,
 *              controlled by the `mdRendered` prop (the toggle lives
 *              in the file tab's toolbar — see FileTabPane)
 *   other    → line-numbered <pre> (no syntax highlight in v1)
 *   binary / >1 MB replies → name + size card with a download link
 *
 * Content comes over WS (``project_file_read``) with the shared
 * files-shared readCache, invalidated by the mtime the tree listing
 * last reported (and by the editor after a save).
 */
import { useEffect, useRef, useState } from "react";
import { Download } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { Markdown } from "@/lib/format-utils/markdown";
import {
  type FileReadResult,
  filesWsRequest,
  latestFileMtime,
  rawFileUrl,
  readCache,
} from "@/lib/state/files-shared";
import styles from "./files-panel.module.css";

export const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "ico"]);

function extOf(path: string): string {
  const base = path.split("/").pop() || "";
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(dot + 1).toLowerCase() : "";
}

function fmtSize(bytes: number): string {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function FileViewer({
  projectId,
  path,
  mdRendered = true,
  onLoaded,
}: {
  projectId: string;
  path: string;
  /** Markdown files: true → <Markdown> render, false → source lines.
   *  The toggle UI lives in the file tab's toolbar. */
  mdRendered?: boolean;
  /** Text files: fires when the read lands (null on failure) so the
   *  tab toolbar can tell editable text from binary / too-large. */
  onLoaded?: (data: FileReadResult | null) => void;
}) {
  const ext = extOf(path);
  if (IMAGE_EXTS.has(ext)) {
    return (
      <div className={styles.viewerScroll}>
        <img
          src={rawFileUrl(projectId, path)}
          alt={path}
          style={{ maxWidth: "100%" }}
        />
      </div>
    );
  }
  if (ext === "pdf") {
    // The browser's built-in PDF viewer renders this in its own
    // isolated process, not the page DOM.
    return (
      <iframe
        src={rawFileUrl(projectId, path)}
        title={path}
        className={styles.pdfFrame}
      />
    );
  }
  return (
    <TextViewer
      projectId={projectId}
      path={path}
      isMarkdown={ext === "md"}
      rendered={mdRendered}
      onLoaded={onLoaded}
    />
  );
}

function TextViewer({
  projectId,
  path,
  isMarkdown,
  rendered,
  onLoaded,
}: {
  projectId: string;
  path: string;
  isMarkdown: boolean;
  rendered: boolean;
  onLoaded?: (data: FileReadResult | null) => void;
}) {
  const { text } = useTranslation();
  const [data, setData] = useState<FileReadResult | null>(null);
  const [failed, setFailed] = useState(false);
  // Ref so a changing callback identity never re-triggers the fetch.
  const onLoadedRef = useRef(onLoaded);
  onLoadedRef.current = onLoaded;

  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    const key = `${projectId}:${path}`;
    const knownMtime = latestFileMtime.get(path);
    const hit = readCache.get(key);
    if (hit && (knownMtime === undefined || hit.mtime === knownMtime)) {
      setData(hit);
      onLoadedRef.current?.(hit);
      return;
    }
    setData(null);
    filesWsRequest<FileReadResult>(
      "project_file_read",
      { project_id: projectId, path },
      "project_file_read_result",
    ).then((res) => {
      if (cancelled) return;
      if (!res || res.error || res.path !== path) {
        setFailed(true);
        onLoadedRef.current?.(null);
        return;
      }
      readCache.set(key, res);
      setData(res);
      onLoadedRef.current?.(res);
    });
    return () => {
      cancelled = true;
    };
  }, [projectId, path]);

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
            href={rawFileUrl(projectId, path)}
            download={path.split("/").pop()}
          >
            <Download size={13} />
            {text("Download", "下载")}
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.viewerScroll}>
      {data.truncated ? (
        <div className={styles.truncatedNote}>
          {text("Truncated at 1 MB", "已在 1 MB 处截断")}
        </div>
      ) : null}
      {isMarkdown && rendered ? (
        // message-content 复用 chat.css 的 markdown 元素样式（标题/列表/表格）
        <div className={`${styles.mdBody} message-content`}>
          <Markdown source={data.content} escapeRawHtml />
        </div>
      ) : (
        <NumberedCode content={data.content} />
      )}
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
          <span className={styles.lineText}>{l || " "}</span>
        </div>
      ))}
    </pre>
  );
}
