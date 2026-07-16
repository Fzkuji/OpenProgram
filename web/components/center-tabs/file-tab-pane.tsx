"use client";

/**
 * FileTabPane — center-column content of a file tab: a 40px toolbar
 * (breadcrumb · Rendered/Source toggle for markdown · download) over
 * the FileViewer body. Only the ACTIVE file tab mounts one of these;
 * the viewer's read-cache keeps tab switches fast.
 */
import { useState } from "react";
import { Download } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { rawFileUrl } from "@/lib/state/files-shared";
import { FileViewer } from "@/components/files/file-viewer";
import styles from "./center-tabs.module.css";

export function FileTabPane({
  projectId,
  path,
}: {
  projectId: string;
  path: string;
}) {
  const { text } = useTranslation();
  const [rendered, setRendered] = useState(true);
  const isMarkdown = (path.split("/").pop() || "").toLowerCase().endsWith(".md");
  const segments = path.split("/");

  return (
    <div className={styles.filePane}>
      <div className={styles.fileToolbar}>
        <span>
          {segments.map((seg, i) => (
            <span key={i}>
              <span
                className={i === segments.length - 1 ? styles.crumbLast : styles.crumb}
              >
                {seg}
              </span>
              {i < segments.length - 1 ? (
                <span className={styles.crumbSep}>›</span>
              ) : null}
            </span>
          ))}
        </span>
        <span className={styles.toolbarSpacer} />
        {isMarkdown ? (
          <span className={styles.mdToggle}>
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
          </span>
        ) : null}
        <a
          className={styles.downloadBtn}
          href={rawFileUrl(projectId, path)}
          download={segments[segments.length - 1]}
          title={text("Download", "下载")}
        >
          <Download size={14} />
        </a>
      </div>
      <div className={styles.fileBody}>
        <FileViewer projectId={projectId} path={path} mdRendered={rendered} />
      </div>
    </div>
  );
}
