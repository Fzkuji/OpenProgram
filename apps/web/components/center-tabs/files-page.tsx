"use client";

import { FileText } from "lucide-react";

import { FileTree } from "@/components/files/file-tree";
import { useTranslation } from "@/lib/i18n";
import { useCurrentProject } from "@/lib/state/files-shared";
import styles from "./center-tabs.module.css";

export function FilesPage() {
  const { text } = useTranslation();
  const project = useCurrentProject();

  if (project === undefined) return <div className={styles.filesPage} />;
  if (!project) {
    return (
      <div className={styles.filesPageEmpty}>
        <FileText size={24} aria-hidden="true" />
        <span>{text("Choose a project to browse files.", "选择项目后可浏览文件。")}</span>
      </div>
    );
  }

  return (
    <div className={styles.filesPage}>
      <FileTree projectId={project.id} />
    </div>
  );
}
