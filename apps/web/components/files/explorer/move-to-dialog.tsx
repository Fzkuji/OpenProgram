"use client";

/**
 * "Move to…": pick any folder in the project without dragging through the
 * tree. The input reads like a path: "Desktop/pro" lists the folders in
 * Desktop whose name has "pro", one directory listing per level, so it
 * works in a project of any size; Tab steps into the highlighted folder.
 * A bare name also searches the whole project when the project is small
 * enough for the server's search. With nothing typed the dialog offers the
 * project root, recent targets and the top-level folders. A typed path
 * that names no folder yet can be created and moved into in one step.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandShortcut,
} from "@/components/ui/command";
import { SolarIcon, type SolarIconName } from "@/components/solar-icons";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { fileResponseMatchesOwner } from "@/lib/files/drafts/file-state-shared";
import { baseOf, joinPath, parentOf } from "../tree/file-tree-query";
import { canMoveInto, matchFolders, recentMoveTargets, splitFolderInput, typedFolder } from "./move-targets";

interface FolderRow { name: string; path?: string; type: string }
interface FolderPage { entries?: FolderRow[]; results?: FolderRow[]; next_cursor?: string | null; snapshot_id?: string | null; error_code?: string }

const PAGE_SIZE = 100;
const MAX_PAGES = 20;

function query(action: string, payload: Record<string, unknown>): Promise<FolderPage | null> {
  return wsRequest<FolderPage>(action, payload, `${action}_result`,
    data => fileResponseMatchesOwner(data as unknown as Record<string, unknown>, payload), 4000);
}

/** The folders directly inside ``dir``. Listings put folders first, so
 *  reading stops at the first file. */
async function listFolders(projectId: string, dir: string): Promise<string[]> {
  const names: string[] = [];
  let cursor: string | null = null;
  let snapshot: string | null = null;
  for (let page = 0; page < MAX_PAGES; page++) {
    const data: FolderPage | null = await query("project_file_tree", {
      project_id: projectId, path: dir, page_size: PAGE_SIZE,
      ...(cursor ? { cursor, snapshot_id: snapshot } : {}),
    });
    const rows = data?.entries ?? [];
    for (const row of rows) if (row.type === "dir") names.push(row.name);
    if (!data?.next_cursor || rows.some(row => row.type !== "dir")) break;
    cursor = data.next_cursor;
    snapshot = data.snapshot_id ?? null;
  }
  return names;
}

export interface MoveToDialogProps {
  open: boolean;
  projectId: string;
  rootName: string;
  /** The entries being moved. */
  paths: string[];
  onOpenChange(open: boolean): void;
  onMove(targetDir: string): void;
  onCreateAndMove(targetDir: string): void;
}

export function MoveToDialog({ open, projectId, rootName, paths, onOpenChange, onMove, onCreateAndMove }: MoveToDialogProps) {
  const { text } = useTranslation();
  const [search, setSearch] = useState("");
  const [highlighted, setHighlighted] = useState("");
  const [listings, setListings] = useState<Record<string, string[] | null>>({});
  const [found, setFound] = useState<string[]>([]);
  const { base, term } = splitFolderInput(search);

  useEffect(() => {
    if (!open) return;
    setSearch("");
    setListings({});
    setFound([]);
  }, [open, projectId]);

  // One listing per level the input reaches, read once per opening.
  useEffect(() => {
    if (!open || base === null || base in listings) return;
    setListings(current => ({ ...current, [base]: null }));
    void listFolders(projectId, base).then(names => setListings(current => ({ ...current, [base]: names })));
  }, [open, projectId, base, listings]);

  // A bare name also searches the whole project; a project too large for
  // the server's search simply has no extra results.
  useEffect(() => {
    setFound([]);
    if (!open || base !== "" || !term) return;
    let live = true;
    const timer = setTimeout(() => {
      void query("project_file_search", { project_id: projectId, path: "", query: term, mode: "fuzzy", type: "dir", page_size: 50 })
        .then(page => { if (live) setFound((page?.results ?? []).map(row => row.path ?? row.name)); });
    }, 150);
    return () => { live = false; clearTimeout(timer); };
  }, [open, projectId, base, term]);

  const first = paths.length ? parentOf(paths[0]) : null;
  const here = first !== null && paths.every(path => parentOf(path) === first) ? first : null;
  const usable = (dir: string) => canMoveInto(paths, dir);
  const blocked = (dir: string) => paths.some(path => dir === path || dir.startsWith(path + "/"));

  const level = base === null ? null : listings[base];
  const nearby = useMemo(() => base === null || !level ? [] : matchFolders(level, term).map(name => joinPath(base, name)), [base, level, term]);
  const elsewhere = found.filter(dir => !nearby.includes(dir));
  const recent = useMemo(() => open ? recentMoveTargets(projectId).filter(usable) : [], [open, projectId, paths]); // eslint-disable-line react-hooks/exhaustive-deps
  const typed = typedFolder(search);
  const exists = typed !== null && (nearby.includes(typed) || found.includes(typed)
    || (listings[parentOf(typed)]?.includes(baseOf(typed)) ?? false));
  const loading = base !== null && level === null;

  const choose = (dir: string) => { onOpenChange(false); onMove(dir); };
  // The trailing note is the folder's parent, unless the group heading
  // already names it; Nova's CommandShortcut keeps it at the right edge.
  const row = (dir: string, icon: SolarIconName = "folder", showParent = true) => {
    const note = dir === here ? text("Current", "当前位置") : showParent && dir ? parentOf(dir) : "";
    return (
      <CommandItem key={dir || "."} value={`dir:${dir}`} disabled={!usable(dir)} onSelect={() => choose(dir)}>
        <SolarIcon name={icon} size={16} motionPreset="none" aria-hidden="true" />
        <span className="min-w-0 truncate">{dir ? baseOf(dir) : rootName}</span>
        {note ? <CommandShortcut className="min-w-0 truncate tracking-normal">{note}</CommandShortcut> : null}
      </CommandItem>
    );
  };

  // Tab steps into the highlighted folder, the way a shell completes a path.
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key !== "Tab" || event.shiftKey || !highlighted.startsWith("dir:")) return;
    const dir = highlighted.slice(4);
    if (!dir) return;
    event.preventDefault();
    setSearch(`${dir}/`);
  };

  const count = paths.length === 1 ? baseOf(paths[0]) : text(`${paths.length} items`, `${paths.length} 项`);
  const nothing = Boolean(search) && !loading && !nearby.length && !elsewhere.length;
  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}
      title={text(`Move ${count} to…`, `将 ${count} 移动到…`)}
      description={text("Choose a folder in this project", "选择项目中的文件夹")}>
      <Command shouldFilter={false} loop value={highlighted} onValueChange={setHighlighted} onKeyDown={onKeyDown}>
        <CommandInput value={search} onValueChange={setSearch}
          placeholder={text(`Move ${count} to… (type a name or a path)`, `将 ${count} 移动到…（输入名称或路径）`)} />
        <CommandList>
          {nothing && !typed ? <CommandEmpty>{text("No folders match", "没有匹配的文件夹")}</CommandEmpty> : null}
          {!search ? <>
            <CommandGroup heading={text("Project", "项目")}>{row("")}</CommandGroup>
            {recent.length ? <CommandGroup heading={text("Recent", "最近")}>{recent.map(dir => row(dir, "history"))}</CommandGroup> : null}
          </> : null}
          {base && !term ? (
            <CommandGroup heading={text("This folder", "这个文件夹")}>{row(base, "folder-open")}</CommandGroup>
          ) : null}
          {nearby.length ? (
            <CommandGroup heading={base ? base : text("Folders", "文件夹")}>{nearby.map(dir => row(dir, "folder", false))}</CommandGroup>
          ) : null}
          {elsewhere.length ? (
            <CommandGroup heading={text("Elsewhere in the project", "项目中的其他位置")}>{elsewhere.map(dir => row(dir))}</CommandGroup>
          ) : null}
          {typed && !loading && !exists && !blocked(typed) ? (
            <CommandGroup heading={text("New folder", "新建文件夹")}>
              <CommandItem value={`new:${typed}`} onSelect={() => { onOpenChange(false); onCreateAndMove(typed); }}>
                <SolarIcon name="add-folder" size={16} motionPreset="none" aria-hidden="true" />
                <span className="min-w-0 truncate">{text(`Create “${typed}” and move here`, `新建“${typed}”并移到这里`)}</span>
              </CommandItem>
            </CommandGroup>
          ) : null}
        </CommandList>
        <div className="flex items-center gap-3 border-t border-border px-3 py-2 text-fs-xs text-text-muted">
          <span>{text("Enter: move here", "Enter：移到这里")}</span>
          <span>{text("Tab: open folder", "Tab：进入文件夹")}</span>
        </div>
      </Command>
    </CommandDialog>
  );
}
