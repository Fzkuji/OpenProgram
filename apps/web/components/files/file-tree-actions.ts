"use client";
import type { Dispatch, SetStateAction } from "react";
import { hasDocumentDraftsForPath } from "@/lib/files/file-drafts";
import { clearFileDraftsForPath, loadFileDraftsForPath, runServerRenameWithDrafts, type ServerRenameResult } from "@/lib/files/files-shared";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { treeClipboard } from "./tree-context-menu";
import { copyText } from "./explorer-header";
import { baseOf, joinPath, parentOf, projectAbsPath } from "./file-tree-query";
import { asServerRenameResult, type FileOperation } from "./file-tree-operation";

type Selection = { path: string; type: "file" | "dir" } | null;
type Creation = { dir: string; kind: "file" | "dir" } | null;
interface FileTreeActionsContext {
  projectId: string;
  text: (en: string, zh: string) => string;
  selected: Selection;
  creating: Creation;
  setFilter: (value: string) => void;
  setSearchOpen: (value: boolean) => void;
  expandChain: (path: string) => void;
  setCreating: Dispatch<SetStateAction<Creation>>;
  setSelected: Dispatch<SetStateAction<Selection>>;
  setDetailsPath: Dispatch<SetStateAction<string | null>>;
  setRenaming: Dispatch<SetStateAction<string | null>>;
  recordNavigation: (path: string, type: "file" | "dir") => void;
  openFile: (path: string) => void;
  fileOp: FileOperation;
}

/** Callbacks capture the current render, while operations retain their own lifecycle. */
export function createFileTreeActions({
  projectId,
  text,
  selected,
  creating,
  setFilter,
  setSearchOpen,
  expandChain,
  setCreating,
  setSelected,
  setDetailsPath,
  setRenaming,
  recordNavigation,
  openFile,
  fileOp,
}: FileTreeActionsContext) {
  function startCreate(kind: "file" | "dir", dir?: string) {
    const target =
      dir ?? (selected ? (selected.type === "dir" ? selected.path : parentOf(selected.path)) : "");
    setFilter(""); // the editable row only renders in tree mode
    setSearchOpen(false);
    expandChain(target);
    setCreating({ dir: target, kind });
  }

  async function commitCreate(name: string) {
    if (!creating) return;
    const { dir, kind } = creating;
    setCreating(null);
    const full = joinPath(dir, name);
    const result = await fileOp("create", { path: full, kind }, [dir]);
    if (result.status === "ready" && kind === "file") {
      recordNavigation(full, "file");
      openFile(full);
    }
  }

  /** After a rename/move, any open center file tab at the old path —
   *  or under it when a directory moved — follows to the new path. */
  function retargetOpenTabs(oldPath: string, newPath: string) {
    setSelected(current => current && (current.path === oldPath || current.path.startsWith(oldPath + "/")) ? { ...current, path: newPath + current.path.slice(oldPath.length) } : current);
    setDetailsPath(current => current && (current === oldPath || current.startsWith(oldPath + "/")) ? newPath + current.slice(oldPath.length) : current);
    const s = useCenterTabs.getState();
    for (const t of [...s.tabs]) {
      if (t.kind !== "file" || t.projectId !== projectId || !t.path) continue;
      if (t.path === oldPath) {
        s.retargetFileTab(t.id, projectId, newPath);
      } else if (t.path.startsWith(oldPath + "/")) {
        s.retargetFileTab(t.id, projectId, newPath + t.path.slice(oldPath.length));
      }
    }
  }

  async function commitRename(oldPath: string, name: string) {
    setRenaming(null);
    if (name === baseOf(oldPath)) return;
    const dir = parentOf(oldPath);
    const newPath = joinPath(dir, name);
    const result = await runServerRenameWithDrafts(
      projectId,
      oldPath,
      newPath,
      async (): Promise<ServerRenameResult> => {
        const result = await fileOp("rename", { path: oldPath, new_path: newPath }, [dir]);
        return asServerRenameResult(result);
      },
      async (): Promise<ServerRenameResult> => {
        const result = await fileOp("rename", { path: newPath, new_path: oldPath }, [dir]);
        return asServerRenameResult(result, "recovery_required");
      },
    );
    if (result.ok) retargetOpenTabs(oldPath, newPath);
    else if (result.message) window.alert(result.message);
  }

  async function copyPathTo(rel: string, absolute: boolean) {
    if (!absolute) return copyText(rel);
    const root = await projectAbsPath(projectId);
    if (root) copyText(`${root}/${rel}`);
  }

  /** Paste destination: the right-clicked dir itself, or the parent
   *  dir of a right-clicked file — always `<target>/<basename(src)>`. */
  async function pasteInto(targetDir: string) {
    const clip = treeClipboard.current;
    if (!clip || clip.projectId !== projectId) return;
    const dest = joinPath(targetDir, baseOf(clip.path));
    if (dest === clip.path) return;
    if (clip.op === "cut") {
      const result = await runServerRenameWithDrafts(
        projectId,
        clip.path,
        dest,
        async (): Promise<ServerRenameResult> => {
          const result = await fileOp("rename", { path: clip.path, new_path: dest }, [parentOf(clip.path), targetDir]);
          return asServerRenameResult(result);
        },
        async (): Promise<ServerRenameResult> => {
          const result = await fileOp("rename", { path: dest, new_path: clip.path }, [parentOf(clip.path), targetDir]);
          return asServerRenameResult(result, "recovery_required");
        },
      );
      if (result.ok) {
        treeClipboard.current = null;
        retargetOpenTabs(clip.path, dest);
      } else if (result.message) window.alert(result.message);
    } else {
      await fileOp("copy", { path: clip.path, new_path: dest }, [targetDir]);
    }
  }

  async function doDelete(path: string) {
    if (await hasDocumentDraftsForPath(projectId, path)) {
      window.alert(text(
        "This file or folder has pending document changes. Resolve them in the document window before deleting.",
        "此文件或文件夹中存在尚未保存的修改，请先在文件窗口处理后再删除。",
      ));
      return;
    }
    const drafts = await loadFileDraftsForPath(projectId, path);
    const hasDraft = drafts.length > 0;
    let retainDrafts = false;
    if (hasDraft) {
      const choice = window.prompt(text(
        "Unsaved changes: type save, export, or discard to continue deleting.",
        "存在未保存修改：请输入 save（保存）、export（导出保留）或 discard（丢弃）后继续删除。",
      ), "discard")?.trim().toLowerCase();
      if (choice === "save") {
        for (const entry of drafts) {
          const saved = await fileOp("write", {
            path: entry.path,
            content: entry.draft.draft,
            expected_mtime: entry.draft.baselineMtime,
            baseline_revision: entry.draft.baselineRevision,
          }, [parentOf(entry.path)]);
          if (saved.status !== "ready") return;
        }
      } else if (choice === "export") {
        // Browsers do not confirm download completion. Keep the durable copy.
        retainDrafts = true;
        try {
          for (const entry of drafts) {
            const blob = new Blob([entry.draft.draft], { type: "text/plain;charset=utf-8" });
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = entry.path.replaceAll("/", "__");
            anchor.click();
            URL.revokeObjectURL(url);
          }
        } catch {
          window.alert(text("Unable to export every local draft; deletion was cancelled.", "无法导出全部本地草稿；已取消删除。"));
          return;
        }
      } else if (choice !== "discard") {
        return;
      }
    }
    const deleted = await fileOp("delete", { path }, [parentOf(path)]);
    if (deleted.status !== "ready") return;
    setSelected(current => current && (current.path === path || current.path.startsWith(path + "/")) ? { path: parentOf(path), type: "dir" } : current);
    setDetailsPath(current => current && (current === path || current.startsWith(path + "/")) ? null : current);
    if (hasDraft && !retainDrafts) {
      const cleared = await clearFileDraftsForPath(projectId, path);
      if (!cleared.ok) {
        window.alert(cleared.message ?? text("Unable to discard the local draft; tabs remain open.", "无法丢弃本地草稿；文件标签仍保持打开。"));
        return;
      }
    }
    // Close any center tab now pointing at a deleted file (the path
    // itself, or anything under a deleted dir).
    const s = useCenterTabs.getState();
    for (const t of [...s.tabs]) {
      if (
        t.kind === "file" &&
        t.projectId === projectId &&
        t.path &&
        (t.path === path || t.path.startsWith(path + "/"))
      ) {
        s.closeTab(t.id);
      }
    }
  }

  return { startCreate, commitCreate, commitRename, copyPathTo, pasteInto, doDelete };
}
