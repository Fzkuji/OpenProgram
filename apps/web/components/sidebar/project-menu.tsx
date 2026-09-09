"use client";
import { useRef, useState, type ReactNode, type MouseEvent } from "react";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { MoreHorizontal, Check, ChevronRight, MessageSquarePlus, FolderOpen, Pencil, PanelsTopLeft, FolderSearch, GitBranch, Archive, FolderMinus } from "lucide-react";
import { MENU_PANEL, MENU_SEPARATOR, itemCls } from "@/components/chat/top-bar/menu-styles";
import { useTranslation } from "@/lib/i18n";
import { useRecentsView, setRecentsView } from "@/lib/prefs/recents-view";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { PinIcon, type AnimatedNavIconHandle } from "@/components/animated-icons";
import { ProjectEditor, type EditableProject } from "./project-editor";
import { useSidebarMenu } from "./use-sidebar-menu";

import { sidebarProjectActionClass } from "./nav-classes";
import { SectionHeader } from "./section-header";
import { Input } from "@/components/ui/input";
import styles from "./project-settings.module.css";

import { wsRequest } from "@/lib/net/ws-request";
import { ProjectOperationDialog, type ProjectOperation } from "./project-operation-dialog";

const item = itemCls(false) + " outline-none " + styles.menuItem;
export function ProjectMenu({project, children, onOpen, onNewSession, onSaved}: {
  project: EditableProject;
  children: (trigger: ReactNode) => ReactNode;
  onOpen: () => void; onNewSession: () => void; onSaved: (project:EditableProject) => void;
}) {
  const { text } = useTranslation();
  const view = useRecentsView();
  const menu = useSidebarMenu();
  const open = menu.open;
  const [editing,setEditing] = useState(false);
  const [addingSection,setAddingSection] = useState(false);
  const [operation,setOperation] = useState<ProjectOperation|null>(null);
  const [error,setError] = useState("");
  const pinned = view.pinnedProjects.includes(project.id);
  const pinRef = useRef<AnimatedNavIconHandle>(null);
  function selectSection(section: string) {
    setRecentsView({ projectSections: {...view.projectSections,[project.id]:section} });
  }
  const revealFolder = async()=>{setError("");try{const result=await wsRequest<{ok?:boolean;error?:string}>("project_file_reveal",{project_id:project.id,path:""},"project_file_reveal_result");if(!result?.ok)throw new Error(result?.error||text("Could not reveal folder", "无法显示文件夹"));}catch(err){setError(String(err instanceof Error?err.message:err));}};
  function togglePin() {
    setRecentsView({pinnedProjects:pinned?view.pinnedProjects.filter(id=>id!==project.id):[...view.pinnedProjects,project.id]});
  }
  function showMenu(event: MouseEvent<HTMLElement>) {
    menu.show(event, [
      { id: "new-chat", label: text("New chat", "新建聊天"), onSelect: onNewSession },
      { id: "open", label: text("Open project", "打开项目"), onSelect: onOpen },
      { id: "pin", label: pinned ? text("Unpin", "取消置顶") : text("Pin", "置顶"), onSelect: togglePin },
      { id: "edit", label: text("Edit project", "编辑项目"), onSelect: () => setEditing(true) },
      { id: "section", label: text("Section", "分区"), separatorBefore: true, children: [
        ...["", ...view.projectSectionNames].map((section, index) => ({ id: `section:${index}`, label: section || text("Projects", "项目"), checked: (view.projectSections[project.id] || "") === section, onSelect: () => selectSection(section) })),
        { id: "new-section", label: text("New section…", "新建分区…"), separatorBefore: true, onSelect: () => setAddingSection(true) },
      ] },
      { id: "reveal", label: text("Reveal in file manager", "在文件管理器中显示"), separatorBefore: true, onSelect: revealFolder },
      { id: "worktree", label: text("Create permanent worktree", "创建持久 worktree"), onSelect: () => setOperation("create_project_worktree") },
      { id: "archive", label: text("Archive chats", "归档聊天"), onSelect: () => setOperation("archive_project_chats") },
      ...(!project.is_default ? [{ id: "remove", label: text("Remove project", "移除项目"), onSelect: () => setOperation("remove_project") }] : []),
    ]);
  }
  return <>
    <Menu.Root open={open && !menu.native} onOpenChange={menu.onOpenChange}>
      <Menu.Trigger asChild><span aria-hidden="true" style={{position:"fixed",left:menu.point.x,top:menu.point.y,width:0,height:0,pointerEvents:"none"}} /></Menu.Trigger>
      <div onContextMenu={showMenu}>
        {children(<button data-state={open ? "open" : "closed"} aria-haspopup="menu" aria-expanded={open} data-active={editing || addingSection || operation !== null} type="button" aria-label={text(`Options for ${project.name}`, `${project.name} 的选项`)} onPointerDown={event=>event.stopPropagation()} onClick={event=>{event.stopPropagation();if(open)menu.close();else showMenu(event);}} className={styles.trigger+" "+sidebarProjectActionClass+" text-text-muted opacity-0 group-hover:opacity-100 focus:opacity-100"}><MoreHorizontal size={14}/></button>)}
      </div>
      <Menu.Portal><Menu.Content side="bottom" align="start" sideOffset={4} onCloseAutoFocus={event=>event.preventDefault()} className={MENU_PANEL+" "+styles.menu+" min-w-[220px]"}>
        <Menu.Item className={item} onSelect={onNewSession}><MessageSquarePlus size={14} className={styles.menuIcon}/>{text("New chat", "新建聊天")}</Menu.Item>
        <Menu.Item className={item} onSelect={onOpen}><FolderOpen size={14} className={styles.menuIcon}/>{text("Open project", "打开项目")}</Menu.Item>
        <Menu.Item className={item}
          onMouseEnter={()=>pinRef.current?.startAnimation()} onMouseLeave={()=>pinRef.current?.stopAnimation()}
          onFocus={()=>pinRef.current?.startAnimation()} onBlur={()=>pinRef.current?.stopAnimation()}
          onSelect={togglePin}><PinIcon ref={pinRef} size={14} className={styles.menuIcon} aria-hidden="true"/>{pinned?text("Unpin", "取消置顶"):text("Pin", "置顶")}</Menu.Item>
        <Menu.Item className={item} onSelect={()=>setEditing(true)}><Pencil size={14} className={styles.menuIcon}/>{text("Edit project", "编辑项目")}</Menu.Item>
        <Menu.Separator className={MENU_SEPARATOR}/>
        <Menu.Sub><Menu.SubTrigger className={item}><PanelsTopLeft size={14} className={styles.menuIcon}/><span className="flex-1">{text("Section", "分区")}</span><ChevronRight size={14}/></Menu.SubTrigger><Menu.Portal><Menu.SubContent className={MENU_PANEL+" "+styles.menu+" min-w-[180px]"}>
          {["",...view.projectSectionNames].map(section=><Menu.Item key={section} className={item} onSelect={()=>selectSection(section)}><span className="flex-1">{section||text("Projects", "项目")}</span>{(view.projectSections[project.id]||"")===section&&<Check size={14}/>}</Menu.Item>)}
          <Menu.Separator className={MENU_SEPARATOR}/>
          <Menu.Item className={item} onSelect={()=>setAddingSection(true)}>{text("New section…", "新建分区…")}</Menu.Item>
        </Menu.SubContent></Menu.Portal></Menu.Sub>
        <Menu.Separator className={MENU_SEPARATOR}/>
        <Menu.Item className={item} onSelect={revealFolder}><FolderSearch size={14} className={styles.menuIcon}/>{text("Reveal in file manager", "在文件管理器中显示")}</Menu.Item>
        <Menu.Item className={item} onSelect={()=>setOperation("create_project_worktree")}><GitBranch size={14} className={styles.menuIcon}/>{text("Create permanent worktree", "创建持久 worktree")}</Menu.Item>
        <Menu.Item className={item} onSelect={()=>setOperation("archive_project_chats")}><Archive size={14} className={styles.menuIcon}/>{text("Archive chats", "归档聊天")}</Menu.Item>
        {!project.is_default&&<Menu.Item className={item} onSelect={()=>setOperation("remove_project")}><FolderMinus size={14} className={styles.menuIcon}/>{text("Remove project", "移除项目")}</Menu.Item>}
      </Menu.Content></Menu.Portal>
    </Menu.Root>
    {operation&&<ProjectOperationDialog project={project} operation={operation} onClose={()=>setOperation(null)} onSaved={onSaved}/>}
    {error&&<Dialog open onOpenChange={open=>{if(!open)setError("");}}><DialogContent className={styles.dialog}><DialogTitle>{text("Project action failed", "项目操作失败")}</DialogTitle><DialogDescription>{error}</DialogDescription><Button onClick={()=>setError("")}>{text("Close", "关闭")}</Button></DialogContent></Dialog>}
    {addingSection&&<SectionNameDialog onClose={()=>setAddingSection(false)} onSave={name=>setRecentsView({projectSectionNames:[...view.projectSectionNames,name],projectSections:{...view.projectSections,[project.id]:name}})}/>}
    {editing&&<ProjectEditor project={project} onClose={()=>setEditing(false)} onSaved={onSaved}/>}
  </>;
}

export function ProjectSectionHeading({ section, collapsed, onToggle, actions }: {
  section: string; collapsed: boolean; onToggle: () => void; actions?: ReactNode;
}) {
  const {text} = useTranslation();
  const view = useRecentsView();
  const [renaming,setRenaming] = useState(false);
  const custom = section !== "" && section !== "__pinned__";
  const title = section === "__pinned__" ? text("Pinned", "置顶") : section || text("Projects", "项目");
  return <><SectionHeader name={title} collapsible collapsed={collapsed} onToggle={onToggle} actions={<>
    {actions}
    {custom && <Menu.Root><Menu.Trigger asChild><button type="button" aria-label={text(`Options for section ${section}`, `分区 ${section} 的选项`)}><MoreHorizontal size={15}/></button></Menu.Trigger>
      <Menu.Portal><Menu.Content className={MENU_PANEL+" "+styles.menu+" min-w-[180px]"}>
        <Menu.Item className={item} onSelect={()=>setRenaming(true)}>{text("Rename section", "重命名分区")}</Menu.Item>
        <Menu.Item className={item} onSelect={()=>setRecentsView({projectSectionNames:view.projectSectionNames.filter(value=>value!==section),projectSections:Object.fromEntries(Object.entries(view.projectSections).filter(([,value])=>value!==section))})}>{text("Remove section", "移除分区")}</Menu.Item>
      </Menu.Content></Menu.Portal>
    </Menu.Root>}
  </>}/>
  {renaming&&<SectionNameDialog initialName={section} onClose={()=>setRenaming(false)} onSave={name=>setRecentsView({projectSectionNames:view.projectSectionNames.map(value=>value===section?name:value),projectSections:Object.fromEntries(Object.entries(view.projectSections).map(([id,value])=>[id,value===section?name:value]))})}/>}
  </>;
}


function SectionNameDialog({initialName="",onSave,onClose}:{initialName?:string;onSave:(name:string)=>void;onClose:()=>void}) {
  const {text} = useTranslation();
  const view = useRecentsView();
  const [name,setName] = useState(initialName);
  const [error,setError] = useState("");
  return <Dialog open onOpenChange={open=>{if(!open)onClose();}}><DialogContent className={styles.dialog}>
    <DialogTitle>{initialName?text("Rename section", "重命名分区"):text("New section", "新建分区")}</DialogTitle>
    <DialogDescription>{text("Organize projects under a named section.", "使用命名分区整理项目。")}</DialogDescription>
    <form className="grid gap-3" onSubmit={event=>{event.preventDefault();const value=name.trim();if(!value||value==="__pinned__"||(value!==initialName&&view.projectSectionNames.includes(value))){setError(text("Choose a unique section name.", "请输入不重复的分区名称。"));return;}try{onSave(value);onClose();}catch{setError(text("Could not save this section.", "无法保存分区。"));}}}>
      <label className="grid gap-1 text-sm">{text("Section name", "分区名称")}<Input value={name} maxLength={80} onChange={event=>setName(event.target.value)} className={styles.field} required/></label>
      {error&&<p role="alert" className="text-sm text-red-500">{error}</p>}
      <div className="flex justify-end gap-2"><Button type="button" variant="secondary" onClick={onClose}>{text("Cancel", "取消")}</Button><Button type="submit">{text("Save", "保存")}</Button></div>
    </form>
  </DialogContent></Dialog>;
}
