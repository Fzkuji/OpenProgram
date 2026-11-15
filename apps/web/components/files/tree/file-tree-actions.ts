"use client";
import type { Dispatch, SetStateAction } from "react";
import { hasDocumentDraftsForPath } from "@/lib/files/drafts/file-drafts";
import { clearFileDraftsForPath, loadFileDraftsForPath, runServerRenameWithDrafts, type ServerRenameResult } from "@/lib/files/files-shared";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { treeClipboard } from "../explorer/tree-context-menu";
import { copyText } from "../explorer/explorer-header";
import { baseOf, joinPath, parentOf, projectAbsPath } from "./file-tree-query";
import { asServerRenameResult, type FileOperation } from "./file-tree-operation";
import { showToast } from "@/lib/format-utils/toast";

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
    if (name === baseOf(oldPath)) return;
    const dir = parentOf(oldPath);
    await moveOne(oldPath, joinPath(dir, name), [dir]);
  }

  async function copyPathTo(rel: string, absolute: boolean) {
    if (!absolute) return copyText(rel);
    const root = await projectAbsPath(projectId);
    if (root) copyText(`${root}/${rel}`);
  }

  /** Move one entry on the server, carrying unsaved drafts and open tabs. */
  async function moveOne(from: string, to: string, refresh: string[]): Promise<boolean> {
    const result = await runServerRenameWithDrafts(
      projectId,
      from,
      to,
      async (): Promise<ServerRenameResult> => asServerRenameResult(await fileOp("rename", { path: from, new_path: to }, refresh)),
      async (): Promise<ServerRenameResult> => asServerRenameResult(await fileOp("rename", { path: to, new_path: from }, refresh), "recovery_required"),
    );
    if (result.ok) retargetOpenTabs(from, to);
    else if (result.message) showToast(result.message, { tone: "error" });
    return result.ok;
  }

  /** Move entries into ``targetDir``. A move that would not change the
   *  path, or would put a folder inside itself, is skipped. The toast's
   *  Undo moves everything back. Returns whether every move landed. */
  async function moveEntries(paths: string[], targetDir: string): Promise<boolean> {
    const moved: [string, string][] = [];
    let complete = true;
    for (const from of paths) {
      const to = joinPath(targetDir, baseOf(from));
      if (to === from || targetDir === from || targetDir.startsWith(from + "/")) continue;
      if (await moveOne(from, to, [parentOf(from), targetDir])) moved.push([from, to]);
      else complete = false;
    }
    if (moved.length) {
      const where = targetDir || text("the project root", "项目根目录");
      showToast(
        moved.length === 1
          ? text(`Moved ${baseOf(moved[0][0])} to ${where}`, `已将 ${baseOf(moved[0][0])} 移到 ${where}`)
          : text(`Moved ${moved.length} items to ${where}`, `已将 ${moved.length} 项移到 ${where}`),
        {
          duration: 6000,
          action: {
            label: text("Undo", "撤销"),
            run: () => void (async () => {
              for (const [from, to] of [...moved].reverse()) await moveOne(to, from, [parentOf(from), targetDir]);
            })(),
          },
        },
      );
    }
    return complete;
  }

  /** Paste destination: the right-clicked dir itself, or the parent
   *  dir of a right-clicked file — always `<target>/<basename(src)>`. */
  async function pasteInto(targetDir: string) {
    const clip = treeClipboard.current;
    if (!clip || clip.projectId !== projectId) return;
    if (clip.op === "cut") {
      await moveEntries(clip.paths, targetDir);
      treeClipboard.current = null;
      return;
    }
    for (const path of clip.paths) {
      const dest = joinPath(targetDir, baseOf(path));
      if (dest !== path) await fileOp("copy", { path, new_path: dest }, [targetDir]);
    }
  }

  async function doDelete(path: string) {
    if (await hasDocumentDraftsForPath(projectId, path)) {
      showToast(text(
        "This file or folder has pending document changes. Resolve them in the document window before deleting.",
        "此文件或文件夹中存在尚未保存的修改，请先在文件窗口处理后再删除。",
      ), { tone: "error" });
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
          showToast(text("Unable to export every local draft; deletion was cancelled.", "无法导出全部本地草稿；已取消删除。"), { tone: "error" });
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
        showToast(cleared.message ?? text("Unable to discard the local draft; tabs remain open.", "无法丢弃本地草稿；文件标签仍保持打开。"), { tone: "error" });
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

  return { startCreate, commitCreate, commitRename, copyPathTo, pasteInto, moveEntries, doDelete };
}
