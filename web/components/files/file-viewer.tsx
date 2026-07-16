"use client";

/**
 * FileViewer — center pane of the files panel. Dispatches on the
 * active file's extension:
 *
 *   images   → <img> straight off the backend raw endpoint
 *   markdown → Rendered (existing <Markdown>, marked-based) / Source toggle
 *   other    → line-numbered <pre> (no syntax highlight in v1)
 *   binary / >1 MB replies → name + size card with a download link
 *
 * Content comes over WS (``project_file_read``) with a small in-memory
 * cache invalidated by the mtime the tree listing last reported.
 */
import { useEffect, useState } from "react";
import { Download } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { Markdown } from "@/lib/format-utils/markdown";
import {
  filesWsRequest,
  latestFileMtime,
  rawFileUrl,
} from "@/lib/state/files-panel-store";
import styles from "./files-panel.module.css";

interface ReadResult {
  project_id: string;
  path: string;
  content?: string;
  size: number;
  mtime: number;
  truncated?: boolean;
  binary?: boolean;
  too_large?: boolean;
  error?: string;
}

const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "ico"]);

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

// ponytail: unbounded per-session cache; add LRU if memory ever matters.
const readCache = new Map<string, ReadResult>();

export function FileViewer({
  projectId,
  path,
}: {
  projectId: string;
  path: string;
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
  return <TextViewer projectId={projectId} path={path} isMarkdown={ext === "md"} />;
}

function TextViewer({
  projectId,
  path,
  isMarkdown,
}: {
  projectId: string;
  path: string;
  isMarkdown: boolean;
}) {
  const { text } = useTranslation();
  const [data, setData] = useState<ReadResult | null>(null);
  const [failed, setFailed] = useState(false);
  const [rendered, setRendered] = useState(true); // markdown: Rendered vs Source

  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    const key = `${projectId}:${path}`;
    const knownMtime = latestFileMtime.get(path);
    const hit = readCache.get(key);
    if (hit && (knownMtime === undefined || hit.mtime === knownMtime)) {
      setData(hit);
      return;
    }
    setData(null);
    filesWsRequest<ReadResult>(
      "project_file_read",
      { project_id: projectId, path },
      "project_file_read_result",
    ).then((res) => {
      if (cancelled) return;
      if (!res || res.error || res.path !== path) {
        setFailed(true);
        return;
      }
      readCache.set(key, res);
      setData(res);
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
      {isMarkdown ? (
        <div className={styles.mdToggle}>
          <button
            type="button"
            className={`${styles.mdToggleBtn} ${rendered ? styles.mdToggleActive : ""}`}
            onClick={() => setRendered(true)}
          >
            {text("Rendered", "渲染")}
          </button>
          <button
            type="button"
            className={`${styles.mdToggleBtn} ${!rendered ? styles.mdToggleActive : ""}`}
            onClick={() => setRendered(false)}
          >
            {text("Source", "源码")}
          </button>
        </div>
      ) : null}
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
