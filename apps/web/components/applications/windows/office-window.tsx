"use client";

/**
 * The ONLYOFFICE window: start a blank Word, Excel or PowerPoint file in the
 * current project, or open an Office document already there. The document
 * itself opens in its file tab, where the editor runs.
 */
import { useEffect, useState } from "react";
import { FileText, Presentation, Sheet } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ManageEmptyState, ManagePage, ManagePageHeader, ManageSummary, managePageStyles as shared, ManageLoadingState } from "@/components/ui/manage-page";
import { useCurrentProject } from "@/lib/files/files-shared";
import { useTranslation } from "@/lib/i18n";
import { useApplicationList } from "@/lib/net/application-list";
import { createOfficeDocument, listOfficeDocuments, type OfficeKind } from "@/lib/office/office-documents";
import { fileExtension } from "@/lib/documents/file-formats";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import styles from "./windows.module.css";

const KIND_OF: Record<string, OfficeKind> = {
  docx: "docx", doc: "docx", odt: "docx", xlsx: "xlsx", xls: "xlsx", ods: "xlsx", pptx: "pptx", ppt: "pptx", odp: "pptx",
};
const ICONS = { docx: FileText, xlsx: Sheet, pptx: Presentation };

export function OfficeWindow() {
  const { text } = useTranslation();
  const project = useCurrentProject();
  const { builtin } = useApplicationList();
  const enabled = builtin.find(item => item.id === "office_editor")?.enabled !== false;
  const [documents, setDocuments] = useState<string[] | null>(null);
  const [tooLarge, setTooLarge] = useState(false);
  const [busy, setBusy] = useState<OfficeKind | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!project) return;
    let live = true;
    setDocuments(null);
    listOfficeDocuments(project.id)
      .then(list => { if (live) { setDocuments(list.paths); setTooLarge(list.tooLarge && !list.paths.length); } })
      .catch(reason => { if (live) { setDocuments([]); setError(String(reason?.message ?? reason)); } });
    return () => { live = false; };
  }, [project]);

  const open = (path: string) => { if (project) useCenterTabs.getState().openFileTab(project.id, path); };
  const create = async (kind: OfficeKind) => {
    if (!project) return;
    setBusy(kind);
    setError("");
    try {
      const path = await createOfficeDocument(project.id, kind);
      setDocuments(list => [path, ...(list ?? []).filter(item => item !== path)]);
      open(path);
    } catch (reason) {
      setError(String((reason as Error)?.message ?? reason));
    } finally {
      setBusy(null);
    }
  };

  const kinds: { kind: OfficeKind; title: string; detail: string }[] = [
    { kind: "docx", title: text("Document", "文档"), detail: "Word · .docx" },
    { kind: "xlsx", title: text("Spreadsheet", "表格"), detail: "Excel · .xlsx" },
    { kind: "pptx", title: text("Presentation", "演示文稿"), detail: "PowerPoint · .pptx" },
  ];

  return <ManagePage>
    <ManagePageHeader
      title="ONLYOFFICE"
      toolbar={project ? <ManageSummary>{project.name}</ManageSummary> : null}
    />
    {error && <div role="alert" className={shared.errorBar}>{error}</div>}
    <div className={shared.body}>
      <div className="mx-auto flex w-full max-w-[760px] flex-col gap-6">
        {!enabled && <p className={styles.notice}>{text(
          "ONLYOFFICE is turned off on the Applications page, so documents open only for download.",
          "ONLYOFFICE 已在应用页关闭，文档只能下载原文件。",
        )}</p>}
        <section aria-label={text("New", "新建")}>
          <h2 className={styles.heading}>{text("New", "新建")}</h2>
          <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3">
            {kinds.map(({ kind, title, detail }) => {
              const Icon = ICONS[kind];
              return <button key={kind} type="button" className={styles.kind} data-kind={kind} disabled={!project || busy !== null} onClick={() => void create(kind)}>
                <span className={styles.kindIcon}><Icon size={24} aria-hidden /></span>
                <span className="grid min-w-0 gap-[3px]"><strong>{busy === kind ? text("Creating…", "正在新建…") : title}</strong><small>{detail}</small></span>
              </button>;
            })}
          </div>
        </section>
        <section aria-label={text("Documents", "文档")}>
          <h2 className={styles.heading}>{project ? text(`Documents in ${project.name}`, `${project.name} 中的文档`) : text("Documents", "文档")}</h2>
          {project === undefined || (project && documents === null) ? (
            <ManageLoadingState>{text("Looking for documents…", "正在查找文档…")}</ManageLoadingState>
          ) : !project ? (
            <ManageEmptyState compact icon={<FileText size={20} />} title={text("No project", "没有项目")} description={text("Open a project to create and edit documents in it.", "打开一个项目后，即可在其中新建和编辑文档。")} />
          ) : tooLarge ? (
            <ManageEmptyState compact icon={<FileText size={20} />} title={text("This project is too large to list", "这个项目太大，无法列出文档")} description={text("Open documents from Files, or work in a smaller project to see them here. New documents still work.", "请从文件里打开文档，或在较小的项目里使用，即可在这里看到。新建文档不受影响。")} />
          ) : documents!.length === 0 ? (
            <ManageEmptyState compact icon={<FileText size={20} />} title={text("No Office documents yet", "还没有 Office 文档")} description={text("Start one above; it is saved at the top of the project.", "在上方新建一个，它会保存在项目根目录。")} />
          ) : (
            <ul className={styles.list}>
              {documents!.map(path => {
                const Icon = ICONS[KIND_OF[fileExtension(path)] ?? "docx"];
                const slash = path.lastIndexOf("/");
                return <li key={path}>
                  <Button variant="ghost" className={styles.row} onClick={() => open(path)}>
                    <Icon size={16} aria-hidden data-kind={KIND_OF[fileExtension(path)] ?? "docx"} className={styles.rowIcon} />
                    <span className={styles.rowName}>{path.slice(slash + 1)}</span>
                    {slash > 0 && <span className={styles.rowDir}>{path.slice(0, slash)}</span>}
                  </Button>
                </li>;
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  </ManagePage>;
}
