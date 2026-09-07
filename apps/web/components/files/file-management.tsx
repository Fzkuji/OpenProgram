"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ArrowDownWideNarrow, ChevronRight, Copy, X } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws-request";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { copyText } from "./explorer-header";
import styles from "./files-panel.module.css";

const DEFAULT_SORT = "name:asc:folders:hidden:ignored";
const SORT_PATTERN = /^(name|mtime|size|kind):(asc|desc):(folders|mixed):(hidden|visible):(ignored|tracked)$/;
const preferenceMemory = new Map<string, string>();
export function useFileSort(projectId: string) {
  const key = `files.sort.${projectId}`;
  const read = () => {
    if (preferenceMemory.has(key)) return preferenceMemory.get(key)!;
    try { const value = localStorage.getItem(key); return value && SORT_PATTERN.test(value) ? value : DEFAULT_SORT; }
    catch { return DEFAULT_SORT; }
  };
  const [value, setValue] = useState(read);
  useEffect(() => {
    const sync = () => setValue(read());
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
  return <Popover><PopoverTrigger asChild><button type="button" className={styles.iconBtn} title={text("Sort and display", "排序与显示")}><ArrowDownWideNarrow /></button></PopoverTrigger>
    <PopoverContent align="start" className={styles.fileSortMenu}>
      <label>{text("Sort by", "排序依据")}<select value={fields[0]} onChange={e => change(0, e.target.value)}>
        <option value="name">{text("Name", "名称")}</option><option value="mtime">{text("Modified", "修改时间")}</option><option value="size">{text("Size", "大小")}</option><option value="kind">{text("Type", "类型")}</option>
      </select></label>
      <label>{text("Order", "顺序")}<select value={fields[1]} onChange={e => change(1, e.target.value)}><option value="asc">{text("Ascending", "升序")}</option><option value="desc">{text("Descending", "降序")}</option></select></label>
      {([[2, "folders", "mixed", text("Folders first", "文件夹优先")], [3, "hidden", "visible", text("Show hidden files", "显示隐藏文件")], [4, "ignored", "tracked", text("Show Git-ignored files", "显示 Git 忽略文件")]] as const).map(([index, yes, no, label]) => <label key={index}><span>{label}</span><input type="checkbox" checked={fields[index] === yes} onChange={e => change(index, e.target.checked ? yes : no)} /></label>)}
      <small>{text("Unknown folder sizes stay last within their group. Refresh after calculating sizes to sort again.", "未知大小的文件夹排在同组末尾。计算完成后刷新可重新按大小排序。")}</small>
    </PopoverContent></Popover>;
}

export function FileBreadcrumb({ root, path, onLocate }: { root: string; path: string; onLocate: (path: string) => void }) {
  const { text } = useTranslation();
  const ref = useRef<HTMLElement>(null);
  const [compact, setCompact] = useState(true);
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setCompact(entry.contentRect.width < 540));
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  const parts = path.split("/").filter(Boolean);
  const crumb = (name: string, target: string) => <button type="button" title={target || root} onClick={() => onLocate(target)}>{name}</button>;
  return <nav ref={ref} className={styles.fileBreadcrumb} aria-label={text("File path", "文件路径")}>
    {crumb(root, "")}{compact && parts.length > 1 ? <><ChevronRight /><Popover><PopoverTrigger asChild><button title={text("Parent folders", "上级文件夹")}>…</button></PopoverTrigger><PopoverContent className={styles.fileCrumbMenu}>{parts.slice(0, -1).map((part, i) => <div key={i}>{crumb(part, parts.slice(0, i + 1).join("/"))}</div>)}</PopoverContent></Popover></> : null}
    {parts.map((part, i) => compact && i < parts.length - 1 ? null : <span className={styles.fileCrumbPart} key={i}><ChevronRight />{crumb(part, parts.slice(0, i + 1).join("/"))}</span>)}
  </nav>;
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

// One queue is shared by the sidebar and central Files view. Automatic work
// only starts for visible rows and stops after a bounded number of chunks.
interface SizeJob { projectId: string; path: string; value: SizeResult; listeners: Set<() => void>; running: boolean; cancelled: boolean; generation: number }
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
    job = { projectId, path, value: { state: "unknown" }, listeners: new Set(), running: false, cancelled: false, generation: 0 };
    jobs.set(key, job);
    if (jobs.size > 256) for (const [old, value] of jobs) { if (!value.listeners.size && !value.running) jobs.delete(old); if (jobs.size <= 256) break; }
  }
  return job;
}
async function scan(job: SizeJob, restart = false, priority = false) {
  if (job.running) {
    const queued = queue.findIndex(item => item.job === job);
    if (priority && queued > 0) queue.unshift(queue.splice(queued, 1)[0]);
    return;
  }
  job.running = true; job.cancelled = false;
  const generation = job.generation;
  const task = { job, run: async () => {
    try {
      if (!job.listeners.size || job.cancelled || generation !== job.generation) return;
      if (restart && job.value.token) await fileManagementQuery("project_folder_size", job.projectId, job.path, { operation: "cancel", token: job.value.token });
      if (!restart && job.value.state === "unknown") {
        const cached = await fileManagementQuery<SizeResult>("project_folder_size", job.projectId, job.path);
        if (generation !== job.generation) return;
        if (cached?.error) { publish(job, cached); return; }
        if (cached && cached.state !== "unknown") publish(job, cached);
      }
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
    if (job.value.token) void fileManagementQuery("project_folder_size", projectId, job.path, { operation: "cancel", token: job.value.token });
    publish(job, { ...job.value, token: null, state: job.value.bytes == null ? "unknown" : "cached" });
  }
}
function useFolderSize(projectId: string, path: string, enabled: boolean, priority = false) {
  const job = getJob(projectId, path);
  const value = useSyncExternalStore(listener => { job.listeners.add(listener); return () => { job.listeners.delete(listener); }; }, () => job.value, () => job.value);
  useEffect(() => {
    if (!enabled) return;
    if (job.value.state === "complete") publish(job, { ...job.value, state: "cached" });
    if (["unknown", "cached"].includes(job.value.state)) void scan(job, false, priority);
  }, [enabled, job, priority, job.generation]);
  return { value, start: (restart = false) => void scan(job, restart, true), cancel: () => {
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
  return <span ref={ref} className={styles.folderSize}>{visible ? <VisibleFolderSize projectId={projectId} path={path} /> : "—"}</span>;
}
function VisibleFolderSize({ projectId, path }: { projectId: string; path: string }) {
  const { text } = useTranslation();
  const { value } = useFolderSize(projectId, path, true);
  const partial = !value.complete;
  return <span title={value.error ?? text("Approximate logical size. Open details to calculate or continue.", "文件逻辑大小估计。打开详情可计算或继续统计。")}>{value.bytes == null ? (value.state === "scanning" ? "…" : "—") : `${partial ? "≥ " : "≈ "}${formatFileBytes(value.bytes)}`}</span>;
}
interface FileInfo { type: string; name: string; absolute_path: string; size: number | null; mtime: number; created_at: number | null; permissions: string; link_target?: string; link_status?: string; error?: string }
export function FileDetails({ projectId, path, onClose, inline = false }: { projectId: string; path: string; onClose: () => void; inline?: boolean }) {
  const { text } = useTranslation();
  const [info, setInfo] = useState<FileInfo | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const origin = useRef<HTMLElement | null>(typeof document === "undefined" ? null : document.activeElement as HTMLElement);
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!inline) return;
    panel.current?.focus();
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    const element = panel.current;
    element?.addEventListener("keydown", close);
    return () => { element?.removeEventListener("keydown", close); if (origin.current?.isConnected) origin.current.focus(); };
  }, [inline]);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    void fileManagementQuery<FileInfo>("project_file_info", projectId, path, {}, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      if (!value || value.error) setError(value?.error ?? text("Unable to load details", "无法读取详情"));
      else setInfo(value);
    });
    return () => controller.abort();
  }, [projectId, path, attempt]);
  const folder = useFolderSize(projectId, path, info?.type === "dir", true);
  const field = (label: string, value: string) => <div><dt>{label}</dt><dd>{value}</dd></div>;
  const date = (value: number | null) => value == null ? text("Unavailable", "不可用") : new Date(value * 1000).toLocaleString(undefined, { timeZoneName: "short" });
  const content = <>
    <h3>{info?.name ?? path}</h3>
    {error ? <div role="alert">{error}<button onClick={() => setAttempt(value => value + 1)}>{text("Retry", "重试")}</button></div> : !info ? <p>{text("Loading…", "加载中…")}</p> : <>
      <dl>{field(text("Type", "类型"), info.type)}{info.type === "symlink" ? field(text("Link", "符号链接"), info.link_target ?? info.link_status ?? text("Unavailable", "不可用")) : null}{field(text("Relative path", "相对路径"), path || ".")}{field(text("Path", "路径"), info.absolute_path)}{field(text("Modified", "修改时间"), date(info.mtime))}{field(text("Created", "创建时间"), date(info.created_at))}{field(text("Permissions", "权限"), info.permissions)}
      {info.type !== "dir" ? field(text("Size", "大小"), `${formatFileBytes(info.size ?? 0)} (${info.size ?? 0} B)`) : field(text("Folder size", "文件夹大小"), folder.value.bytes == null ? text("Not calculated", "未计算") : `${folder.value.complete ? "≈ " : "≥ "}${formatFileBytes(folder.value.bytes)}`)}</dl>
      {info.type === "dir" ? <div className={styles.folderSizeDetails} aria-live="polite"><p>{({ unknown: text("Waiting to calculate", "等待计算"), scanning: text("Calculating…", "正在计算…"), complete: text("Complete scan", "完整统计"), cached: folder.value.complete ? text("Cached result · may be outdated", "缓存结果 · 可能已过期") : text("Cached partial result · incomplete", "缓存的部分统计 · 尚未完成"), partial: text("Partial scan · continue to count the remaining entries", "部分统计 · 可继续扫描剩余条目"), incomplete: text("Incomplete · some entries were skipped", "统计不完整 · 部分条目已跳过"), cancelled: text("Cancelled · partial result", "已取消 · 部分统计"), error: folder.value.error } as Record<string, string | undefined>)[folder.value.state]}</p>
        {folder.value.entries != null ? <p>{text("Entries scanned", "已扫描条目")}: {folder.value.entries} · {text("Skipped", "跳过")}: {folder.value.skipped ?? 0}</p> : null}
        {folder.value.updated_at ? <p>{date(folder.value.updated_at)}</p> : null}
        <small>{text("Sums file bytes, including hidden files. Does not follow symbolic links; restricted directories and unreadable entries are skipped. This is not disk usage.", "累计文件字节数，包含隐藏文件。不跟随符号链接；受限目录和不可读条目会跳过。这不是磁盘占用量。")}</small>
        <div className={styles.fileDetailActions}><button onClick={() => folder.start(true)} disabled={folder.value.state === "scanning"}>{text("Recalculate", "重新计算")}</button>{folder.value.token ? <><button onClick={() => folder.start()}>{text("Continue", "继续统计")}</button><button onClick={folder.cancel}>{text("Cancel", "取消统计")}</button></> : null}</div>
      </div> : null}
      <div className={styles.fileDetailActions}><button onClick={() => void copyText(info.absolute_path)}><Copy size={14} />{text("Copy path", "复制路径")}</button><button onClick={() => void copyText(path)}>{text("Copy relative path", "复制相对路径")}</button></div>
    </>}
  </>;
  if (inline) return <aside ref={panel} tabIndex={-1} className={styles.fileDetailsInline} aria-label={text("File details", "文件详情")}><div className={styles.fileDetailHeading}><h2>{text("File details", "文件详情")}</h2><button onClick={onClose} title={text("Close", "关闭")}><X size={16} /></button></div>{content}</aside>;
  return <Dialog open onOpenChange={open => { if (!open) onClose(); }}><DialogContent className={styles.fileDetails} onCloseAutoFocus={event => { event.preventDefault(); if (origin.current?.isConnected) origin.current.focus(); }}><DialogTitle>{text("File details", "文件详情")}</DialogTitle><DialogDescription className="sr-only">{text("Metadata and folder size", "元数据与文件夹大小")}</DialogDescription>{content}</DialogContent></Dialog>;
}
