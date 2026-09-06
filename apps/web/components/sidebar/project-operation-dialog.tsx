"use client";
import { useState } from "react";
import { Input } from "@/components/ui/input";
import styles from "./project-settings.module.css";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { wsRequest } from "@/lib/net/ws-request";
import { useTranslation } from "@/lib/i18n";
import type { EditableProject } from "./project-editor";
export type ProjectOperation = "remove_project" | "archive_project_chats" | "create_project_worktree";
export function ProjectOperationDialog({project,operation,onClose,onSaved}:{project:EditableProject;operation:ProjectOperation;onClose:()=>void;onSaved:(project:EditableProject)=>void}) {
  const {text}=useTranslation();
  const [path,setPath]=useState("");
  const [branch,setBranch]=useState("");
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const worktree=operation==="create_project_worktree";
  const title=worktree?text("Create permanent worktree", "创建持久 worktree"):operation==="remove_project"?text("Remove project", "移除项目"):text("Archive chats", "归档聊天");
  return <Dialog open onOpenChange={open=>{if(!open&&!busy)onClose();}}><DialogContent className={styles.dialog}>
    <DialogTitle>{title}</DialogTitle>
    <DialogDescription>{worktree?text("Create a new branch from the project's current commit in a separate folder. Uncommitted changes stay in the original folder.", "从项目当前提交创建新分支，使用独立文件夹。未提交的改动保留在原文件夹。"):operation==="remove_project"?text("Hide this project from the sidebar. Files and chats are preserved. Restore it from Projects or reopen its folder.", "从侧边栏隐藏该项目，保留文件和聊天。可从项目页面恢复，或重新打开其文件夹。"):text("Archive all chats in this project. They remain available under the Archived filter, where you can unarchive them.", "归档该项目的所有聊天。可在“已归档”筛选中查看并取消归档。")}</DialogDescription>
    <form className="grid gap-3" onSubmit={async event=>{event.preventDefault();setBusy(true);setError("");try {
      const result=await wsRequest<{ok:boolean;project_id:string;project?:EditableProject;error?:string}>(operation,{project_id:project.id,...(worktree?{path:path.trim(),branch:branch.trim()}:{})},operation+"_result",data=>data.project_id===project.id,130000);
      if(!result?.ok)throw new Error(result?.error||text("No confirmation received. Retry to check the operation.", "未收到确认，请重试以核实操作。"));
      if(result.project?.id===project.id)onSaved(result.project);
      window.dispatchEvent(new Event("project-changed"));onClose();
    }catch(err){setError(err instanceof Error?err.message:String(err));}finally{setBusy(false);}}}>
      {worktree&&<>
        <label className="grid gap-1 text-sm">{text("New folder (absolute path)", "新文件夹（绝对路径）")}<Input className={styles.field} value={path} onChange={e=>setPath(e.target.value)} disabled={busy} required/></label>
        <label className="grid gap-1 text-sm">{text("New branch", "新分支")}<Input className={styles.field} value={branch} onChange={e=>setBranch(e.target.value)} disabled={busy} required/></label>
      </>}
      {error&&<p role="alert" className="text-sm text-red-500">{error}</p>}
      <div className="flex justify-end gap-2"><Button type="button" variant="secondary" disabled={busy} onClick={onClose}>{text("Cancel", "取消")}</Button><Button type="submit" disabled={busy}>{busy?text("Working…", "处理中…"):title}</Button></div>
    </form>
  </DialogContent></Dialog>;
}
