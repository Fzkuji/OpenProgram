"use client";

import { FolderTypeIcon } from "@/components/files/file-type-icon";
import { useState } from "react";
import { FolderPlus } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import styles from "./project-settings.module.css";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useFolderPicker } from "@/components/ui/folder-picker";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws-request";

export interface EditableProject {
  hidden?: boolean; is_default?: boolean; id: string; name: string; path: string; icon?: string; description?: string; source_folders?: string[];
}
const inputClass = styles.field;
const icons = ["", "📁", "💻", "🔬", "📚", "🎮", "🧪", "📊", "🎨", "🌐", "⭐", "📝"];

export function ProjectEditor({ project, onClose, onSaved }: {
  project: EditableProject; onClose: () => void; onSaved: (project: EditableProject) => void;
}) {
  const { text } = useTranslation();
  const [name, setName] = useState(project.name);
  const [icon, setIcon] = useState(project.icon ?? "");
  const [description, setDescription] = useState(project.description ?? "");
  const [folders, setFolders] = useState(project.source_folders ?? []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const { pickFolder, folderPickerDialog, manualOpen } = useFolderPicker();
  async function save(event: React.FormEvent) {
    event.preventDefault(); setError(""); setSaving(true);
    try {
      const result = await wsRequest<{ok:boolean; project?:EditableProject; error?:string}>("update_project", {
        project_id: project.id, patch: {name:name.trim(), icon, description, source_folders:folders},
      }, "project_updated");
      if (!result?.ok || !result.project) throw new Error(result?.error || text("Could not save project", "无法保存项目"));
      onSaved(result.project);
      window.dispatchEvent(new Event("project-changed"));
      onClose();
    } catch (err) { setError(String(err instanceof Error ? err.message : err)); }
    finally { setSaving(false); }
  }
  return <>
    <Dialog open={!manualOpen} onOpenChange={open => { if (!open && !saving && !manualOpen) onClose(); }}>
      <DialogContent className={styles.dialog}>
        <DialogHeader className={styles.heading}><span className={styles.preview} aria-hidden="true">{icon || <FolderTypeIcon size={24}/>}</span><div className={styles.headingCopy}><DialogTitle>{text("Edit project", "编辑项目")}</DialogTitle>
          <DialogDescription>{text("Change the display name, icon, and source folders.", "修改显示名称、图标和源文件夹。")}</DialogDescription></div></DialogHeader>
        <form onSubmit={save} className={styles.form}>
          <label className={styles.label}>{text("Name", "名称")}<Input className={inputClass} value={name} onChange={e=>setName(e.target.value)} maxLength={200} required disabled={saving}/></label>
          <fieldset disabled={saving} className={styles.section}><legend className="text-sm">{text("Icon", "图标")}</legend>
            <div className={styles.iconGrid}>{icons.map(value=><button type="button" key={value} aria-label={value || text("Default folder icon", "默认文件夹图标")} aria-pressed={icon===value} onClick={()=>setIcon(value)} className={styles.iconOption}>{value || <FolderTypeIcon size={18}/>}</button>)}</div>
            <label className={styles.label}>{text("Custom symbol or emoji", "自定义符号或表情")}<Input className={inputClass} value={icon} onChange={e=>setIcon(e.target.value)} maxLength={32}/></label>
          </fieldset>
          <label className={styles.label}>{text("Description", "说明")}<Textarea className={inputClass} value={description} onChange={e=>setDescription(e.target.value)} maxLength={2000} rows={3} disabled={saving}/></label>
          <fieldset disabled={saving} className={styles.section}><legend className="text-sm">{text("Source folders", "源文件夹")}</legend>
            <div className={styles.folder}><FolderTypeIcon size={17}/><div className={styles.folderCopy}>{project.path}<div className={styles.hint}>{text("Main folder", "主文件夹")}</div></div></div>
            {folders.map(folder=><div key={folder} className={styles.folder}><FolderTypeIcon size={17}/><span className={styles.folderCopy}>{folder}</span><button className={styles.remove} type="button" aria-label={text(`Remove ${folder}`, `移除 ${folder}`)} onClick={()=>setFolders(items=>items.filter(item=>item!==folder))}>×</button></div>)}
            <Button type="button" variant="outline" onClick={async()=>{try { const folder=await pickFolder(project.path); if(folder && folder!==project.path) setFolders(items=>[...new Set([...items,folder])]); } catch(err){setError(String(err));}}}><FolderPlus size={16}/>{text("Add folder", "添加文件夹")}</Button>
            <p className={styles.hint}>{text("Additional folders are defaults for chats without their own folder settings. Removing one here does not delete files.", "额外文件夹用于尚未单独设置目录的会话。从此处移除不会删除文件。")}</p>
          </fieldset>
          {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
          <DialogFooter className={styles.footer}><Button type="button" variant="secondary" disabled={saving} onClick={onClose}>{text("Cancel", "取消")}</Button><Button type="submit" disabled={saving || !name.trim()}>{saving ? text("Saving…", "保存中…") : text("Save", "保存")}</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
    {folderPickerDialog}
  </>;
}
