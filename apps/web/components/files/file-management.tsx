"use client";

import { Fragment, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ArrowDownWideNarrow, Check, Copy, X } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { useEffectEvent } from "@/lib/hooks/use-effect-event";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { HoverTip } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import { Breadcrumb, BreadcrumbEllipsis, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbSeparator } from "@/components/ui/breadcrumb";
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator } from "@/components/ui/dropdown-menu";
import { MENU_ITEM_STATES, MENU_PANEL, GROUP_LABEL, MENU_SEPARATOR, itemCls } from "@/components/ui/menu/menu-styles";
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuTrigger } from "@/components/ui/context-menu";
import { MenuRow, type MenuGlyph } from "@/components/ui/menu/menu-parts";
import { copyText } from "./explorer/explorer-header";
import styles from "./files-panel.module.css";

const DEFAULT_SORT = "name:asc:folders:hidden:ignored";
const SORT_PATTERN = /^(name|mtime|size|kind):(asc|desc):(folders|mixed):(hidden|visible):(ignored|tracked)$/;
const preferenceMemory = new Map<string, string>();
function readSort(key: string) {
  if (preferenceMemory.has(key)) return preferenceMemory.get(key)!;
  try { const value = localStorage.getItem(key); return value && SORT_PATTERN.test(value) ? value : DEFAULT_SORT; }
  catch { return DEFAULT_SORT; }
}
export function useFileSort(projectId: string) {
  const key = `files.sort.${projectId}`;
  const [value, setValue] = useState(() => readSort(key));
  useEffect(() => {
    const sync = () => setValue(readSort(key));
    sync();
    const storageSync = () => { preferenceMemory.delete(key); sync(); };
    window.addEventListener("storage", storageSync);
    window.addEventListener("files-preferences", sync);
    return () => { window.removeEventListener("storage", storageSync); window.removeEventListener("files-preferences", sync); };
  }, [key]);
  return [value, (next: string) => {
    preferenceMemory.set(key, next);
    setValue(next);
    try { localStorage.setItem(key, next); } catch { /* Storage can be disabled. */ }
    window.dispatchEvent(new Event("files-preferences"));
  }] as const;
}

export function FileSortMenu({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { text } = useTranslation();
  const fields = value.split(":");
  const change = (index: number, next: string) => {
    const updated = fields.map((item, i) => i === index ? next : item);
    if (index === 0) updated[1] = next === "size" || next === "mtime" ? "desc" : "asc";
    onChange(updated.join(":"));
  };
  const option = (index: number, key: string, glyph: MenuGlyph, label: string, alternate?: string) => <DropdownMenuItem key={key} role={alternate ? "menuitemcheckbox" : "menuitemradio"} aria-checked={fields[index] === key} className={`${itemCls(false)} outline-none data-[highlighted]:bg-accent data-[highlighted]:text-text-bright`} onSelect={event => { event.preventDefault(); change(index, alternate && fields[index] === key ? alternate : key); }}><MenuRow glyph={glyph} label={label} checked={fields[index] === key} /></DropdownMenuItem>;
  return <DropdownMenu modal={false}><HoverTip label={text("Sort and display", "排序与显示")}><DropdownMenuTrigger asChild><Button type="button" variant="ghost" size="icon-sm" aria-label={text("Sort and display", "排序与显示")}><ArrowDownWideNarrow /></Button></DropdownMenuTrigger></HoverTip>
    <DropdownMenuContent align="start" className={`${MENU_PANEL} w-[240px]`}>
      <div role="group" aria-label={text("Sort by", "排序依据")}><div className={GROUP_LABEL}>{text("Sort by", "排序依据")}</div>
        {option(0, "name", "pen", text("Name", "名称"))}{option(0, "mtime", "clock-circle", text("Modified", "修改时间"))}{option(0, "size", "database", text("Size", "大小"))}{option(0, "kind", "file-text", text("Type", "类型"))}
      </div><DropdownMenuSeparator className={MENU_SEPARATOR} />
      <div role="group" aria-label={text("Order", "顺序")}>
        {option(1, "asc", "sort-from-bottom-to-top", text("Ascending", "升序"))}{option(1, "desc", "sort-from-top-to-bottom", text("Descending", "降序"))}
      </div><DropdownMenuSeparator className={MENU_SEPARATOR} />
      {option(2, "folders", "folder", text("Folders first", "文件夹优先"), "mixed")}{option(3, "hidden", "eye", text("Show hidden files", "显示隐藏文件"), "visible")}{option(4, "ignored", "git-branch", text("Show Git-ignored files", "显示 Git 忽略文件"), "tracked")}
    </DropdownMenuContent></DropdownMenu>;

}

const CRUMB_SEPARATOR = 26;
const CRUMB_ELLIPSIS = 20;

export function FileBreadcrumb({ root, path, absolutePath, onLocate }: { root: string; path: string; absolutePath?: string; onLocate: (path: string) => void }) {
  const { text } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const copyGeneration = useRef(0);
  useEffect(() => {
    setCopied(false);
    const copies = copyGeneration;
    return () => { copies.current++; if (copyTimer.current) clearTimeout(copyTimer.current); };
  }, [absolutePath]);
  async function copy(value: string) {
    const generation = ++copyGeneration.current;
    if (!await copyText(value) || generation !== copyGeneration.current) return;
    if (copyTimer.current) clearTimeout(copyTimer.current);
    setCopied(true);
    copyTimer.current = setTimeout(() => setCopied(false), 1500);
  }
  const rootAbsolute = absolutePath === undefined ? undefined : path ? absolutePath.slice(0, -path.length).replace(/\/$/, "") : absolutePath;
  /** A path segment's right-click menu (shadcn ContextMenu at the pointer). */
  function pathMenu(name: string, target: string, segment: React.ReactNode) {
    const absolute = rootAbsolute === undefined ? undefined : target ? `${rootAbsolute.replace(/\/$/, "")}/${target}` : rootAbsolute || "/";
    return <ContextMenu>
      <ContextMenuTrigger asChild>{segment}</ContextMenuTrigger>
      <ContextMenuContent className="min-w-35">
        <ContextMenuItem onSelect={() => { void copy(target || "."); }}><MenuRow glyph="copy" label={text("Copy relative path", "复制相对路径")} /></ContextMenuItem>
        <ContextMenuItem disabled={absolute === undefined} onSelect={() => { if (absolute !== undefined) void copy(absolute); }}><MenuRow glyph="copy" label={text("Copy absolute path", "复制绝对路径")} /></ContextMenuItem>
        <ContextMenuItem onSelect={() => { void copy(name); }}><MenuRow glyph="pen" label={text("Copy name", "复制名称")} /></ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>;
  }
  const ref = useRef<HTMLElement>(null);
  const measureRef = useRef<HTMLSpanElement>(null);
  const parts = path.split("/").filter(Boolean);
  const [{ firstVisible, leadingWidth }, setLayout] = useState<{ firstVisible: number; leadingWidth?: number }>({ firstVisible: Math.max(0, parts.length - 1) });
  useEffect(() => {
    let disposed = false;
    const fit = () => {
      if (disposed || !ref.current || !measureRef.current) return;
      const available = ref.current.clientWidth;
      if (!available) return;
      const widths = Array.from(measureRef.current.children, item => item.getBoundingClientRect().width);
      const rootWidth = Math.min(widths[0], available * .4);
      // Each later item brings a separator: Nova's 14px chevron with the
      // list's 6px gap on both sides. The ellipsis item is 20px.
      const segments = widths.slice(1).map(width => width + CRUMB_SEPARATOR);
      const ellipsis = CRUMB_ELLIPSIS + CRUMB_SEPARATOR;
      let remaining = segments.reduce((sum, width) => sum + width, 0);
      let first = 0;
      if (rootWidth + remaining > available) {
        while (first < segments.length - 1 && rootWidth + ellipsis + remaining > available) {
          remaining -= segments[first++];
        }
      }
      // Use spare space for one readable ancestor fragment instead of
      // discarding an entire segment just because its full name cannot fit.
      let leadingWidth: number | undefined;
      if (first > 0) {
        const spare = available - rootWidth - remaining - (first > 1 ? ellipsis : 0) - CRUMB_SEPARATOR;
        if (spare >= 48) {
          first--;
          leadingWidth = spare;
        }
      }
      setLayout({ firstVisible: first, leadingWidth });
    };
    const observer = new ResizeObserver(fit);
    if (ref.current) observer.observe(ref.current);
    fit();
    void document.fonts?.ready.then(fit);
    return () => { disposed = true; observer.disconnect(); };
  }, [root, path]);
  const crumb = (name: string, target: string, current = false) => pathMenu(name, target, <BreadcrumbLink asChild><button type="button" data-path={target} aria-current={current ? "page" : undefined} className={current ? "text-text-primary" : undefined} onClick={() => onLocate(target)}>{name}</button></BreadcrumbLink>);
  return <><Breadcrumb ref={ref} className={styles.fileBreadcrumb} aria-label={text("File path", "文件路径")}>
    <span ref={measureRef} className={styles.fileBreadcrumbMeasure} aria-hidden="true">{[root, ...parts].map((name, i) => <span key={i}>{name}</span>)}</span>
    <BreadcrumbList className={styles.fileCrumbList}>
      <BreadcrumbItem className={styles.fileCrumbRoot}>{crumb(root, "", parts.length === 0)}</BreadcrumbItem>
      {firstVisible > 0 ? <><BreadcrumbSeparator /><BreadcrumbItem><DropdownMenu modal={false}><DropdownMenuTrigger asChild><button type="button" className={`${styles.fileCrumbMore} flex`} aria-label={text("Parent folders", "上级文件夹")}><BreadcrumbEllipsis /></button></DropdownMenuTrigger><DropdownMenuContent align="start" className={`${MENU_PANEL} ${styles.fileCrumbMenu}`}>{parts.slice(0, firstVisible).map((part, i) => <span key={i} className="contents">{pathMenu(part, parts.slice(0, i + 1).join("/"), <DropdownMenuItem className={`${itemCls(false)} ${MENU_ITEM_STATES}`} onSelect={() => onLocate(parts.slice(0, i + 1).join("/"))}>{part}</DropdownMenuItem>)}</span>)}</DropdownMenuContent></DropdownMenu></BreadcrumbItem></> : null}
      {parts.map((part, i) => i < firstVisible ? null : <Fragment key={i}><BreadcrumbSeparator /><BreadcrumbItem style={i === firstVisible && leadingWidth !== undefined ? { maxWidth: leadingWidth } : undefined}>{crumb(part, parts.slice(0, i + 1).join("/"), i === parts.length - 1)}</BreadcrumbItem></Fragment>)}
    </BreadcrumbList>
  </Breadcrumb><HoverTip label={copied ? text("Copied", "已复制") : text("Copy absolute path", "复制绝对路径")}><Button type="button" variant="ghost" size="icon-sm" disabled={!absolutePath} aria-label={copied ? text("Copied", "已复制") : text("Copy absolute path", "复制绝对路径")} onClick={() => { if (absolutePath) void copy(absolutePath); }}>{copied ? <Check /> : <Copy />}</Button></HoverTip></>;
}

export interface SizeResult { state: string; complete?: boolean; bytes?: number | null; entries?: number; skipped?: number; token?: string | null; updated_at?: number; error?: string }
interface Owned { project_id: string; path: string }
export async function fileManagementQuery<T>(action: string, projectId: string, path: string, extra: Record<string, unknown> = {}, signal?: AbortSignal): Promise<T | null> {
  return wsRequest<T & Owned>(action, { project_id: projectId, path, ...extra }, `${action}_result`, d => d.project_id === projectId && d.path === path, 10000, { signal });
}
export function formatFileBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 4);
  return `${(bytes / 1024 ** exponent).toFixed(1)} ${["B", "KiB", "MiB", "GiB", "TiB"][exponent]}`;
}

// One queue is shared by the sidebar and central Files view. A visible row
// asks once per folder ("measure": the saved sample, else one bounded walk the
// server saves); it asks again only after Refresh or a Files change. Chunked
// start/continue is reserved for Recalculate and Continue in Details.
interface SizeJob { projectId: string; path: string; value: SizeResult; listeners: Set<() => void>; running: boolean; cancelled: boolean; generation: number; needsRefresh: boolean }
const jobs = new Map<string, SizeJob>();
const queue: Array<{ job: SizeJob; run: () => Promise<void> }> = [];
let running = 0;
function pump() {
  while (running < 2 && queue.length) {
    running++;
    void queue.shift()!.run().finally(() => { running--; pump(); });
  }
}
function publish(job: SizeJob, value: SizeResult) {
  // Completeness belongs to the byte sample, independent of queue/cache state.
  job.value = { ...value, complete: value.complete ?? ["complete", "cached"].includes(value.state), ...(value.error ? { state: "error" } : {}) };
  for (const listener of job.listeners) listener();
}
function getJob(projectId: string, path: string) {
  const key = JSON.stringify([projectId, path]);
  let job = jobs.get(key);
  if (!job) {
    job = { projectId, path, value: { state: "unknown" }, listeners: new Set(), running: false, cancelled: false, generation: 0, needsRefresh: false };
    jobs.set(key, job);
    if (jobs.size > 256) for (const [old, value] of jobs) { if (!value.listeners.size && !value.running) jobs.delete(old); if (jobs.size <= 256) break; }
  }
  return job;
}
async function scan(job: SizeJob, restart = false, priority = false, explicit = false) {
  if (job.running) {
    const queued = queue.findIndex(item => item.job === job);
    if (priority && queued > 0) queue.unshift(queue.splice(queued, 1)[0]);
    return;
  }
  job.running = true; job.cancelled = false;
  const generation = job.generation;
  restart ||= job.needsRefresh;
  const task = { job, run: async () => {
    try {
      if (!job.listeners.size || job.cancelled || generation !== job.generation) return;
      if (!explicit) {
        const fresh = restart;
        job.needsRefresh = false;
        if (fresh && job.value.token) void fileManagementQuery("project_folder_size", job.projectId, job.path, { operation: "cancel", token: job.value.token });
        if (fresh) publish(job, { ...job.value, token: null, state: "scanning" });
        const result = await fileManagementQuery<SizeResult>("project_folder_size", job.projectId, job.path, { operation: "measure", ...(fresh ? { fresh: true } : {}) });
        if (generation !== job.generation) { if (result?.token) void fileManagementQuery("project_folder_size", job.projectId, job.path, { operation: "cancel", token: result.token }); return; }
        publish(job, result ?? { ...job.value, state: "error", error: "Size query unavailable" });
        return;
      }
      if (restart && job.value.token) await fileManagementQuery("project_folder_size", job.projectId, job.path, { operation: "cancel", token: job.value.token });
      if (!restart && job.value.state === "unknown") {
        const cached = await fileManagementQuery<SizeResult>("project_folder_size", job.projectId, job.path);
        if (generation !== job.generation) return;
        if (cached?.error) { publish(job, cached); return; }
        if (cached && cached.state !== "unknown") {
          publish(job, cached);
          if (cached.state === "cached" && cached.bytes != null && !cached.token) return;
        }
      }
      job.needsRefresh = false;
      for (let chunk = 0; chunk < 20 && job.listeners.size && !job.cancelled; chunk++) {
        const token = restart && chunk === 0 ? null : job.value.token;
        publish(job, { ...job.value, state: "scanning" });
        const result = await fileManagementQuery<SizeResult>("project_folder_size", job.projectId, job.path, { operation: token ? "continue" : "start", token });
        if (generation !== job.generation) { if (result?.token) void fileManagementQuery("project_folder_size", job.projectId, job.path, { operation: "cancel", token: result.token }); return; }
        publish(job, result ?? { state: "error", error: "Size query unavailable" });
        if (!result?.token) break;
      }
      if ((job.cancelled || !job.listeners.size) && job.value.token) {
        const result = await fileManagementQuery<SizeResult>("project_folder_size", job.projectId, job.path, { operation: "cancel", token: job.value.token });
        publish(job, result ?? { ...job.value, state: "cancelled", token: null });
      }
    } finally { job.running = false; if (generation !== job.generation && job.listeners.size) void scan(job, true); }
  } };
  if (priority) queue.unshift(task); else queue.push(task);
  pump();
}
export function invalidateFolderSizes(projectId: string) {
  for (const job of jobs.values()) if (job.projectId === projectId) {
    job.generation++;
    job.needsRefresh = true;
    if (job.value.token) void fileManagementQuery("project_folder_size", projectId, job.path, { operation: "cancel", token: job.value.token });
    publish(job, { ...job.value, token: null, state: job.value.bytes == null ? "unknown" : "cached" });
  }
}
export function useFolderSize(projectId: string, path: string, enabled: boolean, priority = false) {
  const job = getJob(projectId, path);
  const value = useSyncExternalStore(listener => { job.listeners.add(listener); return () => { job.listeners.delete(listener); }; }, () => job.value, () => job.value);
  useEffect(() => {
    if (!enabled) return;
    if (job.value.state === "complete") publish(job, { ...job.value, state: "cached" });
    if (job.value.state === "unknown" || job.needsRefresh) void scan(job, false, priority);
  }, [enabled, job, priority, job.generation]);
  return { value, start: (restart = false) => void scan(job, restart, true, true), cancel: () => {
    job.cancelled = true;
    if (!job.running && job.value.token) void fileManagementQuery<SizeResult>("project_folder_size", projectId, path, { operation: "cancel", token: job.value.token }).then(result => publish(job, result ?? { ...job.value, state: "cancelled", token: null }));
  } };
}
export function FolderSize({ projectId, path }: { projectId: string; path: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return <span ref={ref} className={styles.folderSize}>{visible ? <VisibleFolderSize projectId={projectId} path={path} /> : null}</span>;
}
function VisibleFolderSize({ projectId, path }: { projectId: string; path: string }) {
  const { text } = useTranslation();
  const { value } = useFolderSize(projectId, path, true);
  return <span className={value.state === "scanning" && value.bytes != null ? styles.folderSizeScanning : undefined} data-tip={text("Approximate logical size. Open details to calculate or continue.", "文件逻辑大小估计。打开详情可计算或继续统计。")}>{value.bytes == null ? "" : formatFileBytes(value.bytes)}</span>;
}
interface FileInfo { type: string; name: string; absolute_path: string; size: number | null; mtime: number; created_at: number | null; permissions: string; link_target?: string; link_status?: string; error?: string }
/** Details already read, so the panel opened again shows them at once. */
const detailsRead = new Map<string, FileInfo>();
export function FileDetails({ projectId, path, onClose, inline = false }: { projectId: string; path: string; onClose: () => void; inline?: boolean }) {
  const { text } = useTranslation();
  const detailsKey = `${projectId}\n${path}`;
  const [info, setInfo] = useState<FileInfo | null>(() => detailsRead.get(detailsKey) ?? null);
  // A read that fails is asked again, quietly, with backoff.
  const [attempt, setAttempt] = useState(0);
  const origin = useRef<HTMLElement | null>(typeof document === "undefined" ? null : document.activeElement as HTMLElement);
  const panel = useRef<HTMLElement>(null);
  const closeDetails = useEffectEvent(onClose);
  useEffect(() => {
    if (!inline) return;
    panel.current?.focus();
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") closeDetails(); };
    const element = panel.current;
    const returnFocus = origin.current;
    element?.addEventListener("keydown", close);
    return () => { element?.removeEventListener("keydown", close); if (returnFocus?.isConnected) returnFocus.focus(); };
  }, [inline]);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    void fileManagementQuery<FileInfo>("project_file_info", projectId, path, {}, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      if (!value || value.error) {
        console.warn("file details read failed; reading again", { path, error: value?.error });
        timer = setTimeout(() => setAttempt(n => n + 1), Math.min(30_000, 1000 * 2 ** attempt));
        return;
      }
      detailsRead.set(detailsKey, value);
      setInfo(value);
    });
    return () => { controller.abort(); clearTimeout(timer); };
  }, [projectId, path, attempt, detailsKey]);
  const folder = useFolderSize(projectId, path, info?.type === "dir", true);
  const field = (label: string, value: string) => <div><dt>{label}</dt><dd>{value}</dd></div>;
  const date = (value: number | null) => value == null ? text("Unavailable", "不可用") : new Date(value * 1000).toLocaleString(undefined, { timeZoneName: "short" });
  const content = <>
    <h3>{info?.name ?? path}</h3>
    {!info ? null : <>
      <dl>{field(text("Type", "类型"), info.type)}{info.type === "symlink" ? field(text("Link", "符号链接"), info.link_target ?? info.link_status ?? text("Unavailable", "不可用")) : null}{field(text("Relative path", "相对路径"), path || ".")}{field(text("Path", "路径"), info.absolute_path)}{field(text("Modified", "修改时间"), date(info.mtime))}{field(text("Created", "创建时间"), date(info.created_at))}{field(text("Permissions", "权限"), info.permissions)}
      {info.type !== "dir" ? field(text("Size", "大小"), `${formatFileBytes(info.size ?? 0)} (${info.size ?? 0} B)`) : field(text("Folder size", "文件夹大小"), folder.value.bytes == null ? text("Not calculated", "未计算") : `${folder.value.complete ? "≈ " : "≥ "}${formatFileBytes(folder.value.bytes)}`)}</dl>
      {info.type === "dir" ? <div className={styles.folderSizeDetails} aria-live="polite"><p>{({ unknown: text("Waiting to calculate", "等待计算"), scanning: text("Calculating…", "正在计算…"), complete: text("Complete scan", "完整统计"), cached: folder.value.complete ? text("Cached result · may be outdated", "缓存结果 · 可能已过期") : text("Cached partial result · incomplete", "缓存的部分统计 · 尚未完成"), partial: text("Partial scan · continue to count the remaining entries", "部分统计 · 可继续扫描剩余条目"), incomplete: text("Incomplete · some entries were skipped", "统计不完整 · 部分条目已跳过"), cancelled: text("Cancelled · partial result", "已取消 · 部分统计"), error: undefined } as Record<string, string | undefined>)[folder.value.state]}</p>
        {folder.value.entries != null ? <p>{text("Entries scanned", "已扫描条目")}: {folder.value.entries} · {text("Skipped", "跳过")}: {folder.value.skipped ?? 0}</p> : null}
        {folder.value.updated_at ? <p>{date(folder.value.updated_at)}</p> : null}
        <small>{text("Sums file bytes, including hidden files. Does not follow symbolic links; restricted directories and unreadable entries are skipped. This is not disk usage.", "累计文件字节数，包含隐藏文件。不跟随符号链接；受限目录和不可读条目会跳过。这不是磁盘占用量。")}</small>
        <div className="mt-[8px] flex flex-wrap gap-[6px]"><Button variant="elevated" size="xs" onClick={() => folder.start(true)} disabled={folder.value.state === "scanning"}>{text("Recalculate", "重新计算")}</Button>{folder.value.token ? <><Button variant="elevated" size="xs" onClick={() => folder.start()}>{text("Continue", "继续统计")}</Button><Button variant="elevated" size="xs" onClick={folder.cancel}>{text("Cancel", "取消统计")}</Button></> : null}</div>
      </div> : null}
      <div className="mt-[8px] flex flex-wrap gap-[6px]"><Button variant="elevated" size="xs" onClick={() => void copyText(info.absolute_path)}><Copy size={14} />{text("Copy path", "复制路径")}</Button><Button variant="elevated" size="xs" onClick={() => void copyText(path)}>{text("Copy relative path", "复制相对路径")}</Button></div>
    </>}
  </>;
  if (inline) return <aside ref={panel} tabIndex={-1} className={styles.fileDetailsInline} aria-label={text("File details", "文件详情")}><div className={styles.fileDetailHeading}><h2>{text("File details", "文件详情")}</h2><Button variant="ghost" size="icon-sm" onClick={onClose} title={text("Close", "关闭")}><X size={16} /></Button></div>{content}</aside>;
  return <Dialog open onOpenChange={open => { if (!open) onClose(); }}><DialogContent className={styles.fileDetails} onCloseAutoFocus={event => { event.preventDefault(); if (origin.current?.isConnected) origin.current.focus(); }}><DialogTitle>{text("File details", "文件详情")}</DialogTitle><DialogDescription className="sr-only">{text("Metadata and folder size", "元数据与文件夹大小")}</DialogDescription>{content}</DialogContent></Dialog>;
}
