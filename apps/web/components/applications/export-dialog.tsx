"use client";
/**
 * Export an application as software of its own (programs export,
 * openprogram/programs/_host/ui/export.py): choose a folder, export,
 * then show the result in the file manager.
 */
import { useEffect, useRef, useState } from "react";
import { applicationRequest } from "@/lib/net/applications";
import { useTranslation } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FolderOpenIcon } from "@/components/animated-icons";
import { ApplicationIcon } from "./application-icon";
import type { ApplicationRow } from "./application-rows";
import styles from "./applications-page.module.css";

/** Only a package that needs neither the App's composer nor its projects runs on its own. */
export function exportable(row: ApplicationRow): boolean {
  return Boolean(row.definition) && row.scope === "global" && !row.definition?.capabilities?.includes("chat");
}

interface Exported { path: string; size: number }

const isMac = () => typeof navigator !== "undefined" && /Mac/i.test(navigator.userAgent);

/** The drawn icon as the PNG a macOS icon takes: 1024 px, the tile inside the standard margin. */
async function iconPng(svg: SVGSVGElement | null | undefined): Promise<string | undefined> {
  if (!svg) return undefined;
  const source = new XMLSerializer().serializeToString(svg);
  const image = new Image();
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(source)}`;
  await image.decode();
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1024;
  canvas.getContext("2d")?.drawImage(image, 100, 100, 824, 824);
  return canvas.toDataURL("image/png");
}

export function ExportDialog({ row, onClose, pickFolder }: {
  row: ApplicationRow | null;
  onClose: () => void;
  pickFolder: () => Promise<string | null | undefined>;
}) {
  const { text } = useTranslation();
  const [folder, setFolder] = useState("~/Downloads");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<Exported | null>(null);
  const drawn = useRef<HTMLDivElement>(null);
  useEffect(() => { if (row) { setBusy(false); setError(""); setDone(null); } }, [row]);
  if (!row) return null;
  const file = isMac() ? `${row.title}.app` : row.title;
  const name = done ? done.path.split(/[\\/]/).pop() ?? file : file;
  const where = done ? done.path.slice(0, done.path.length - name.length - 1) : "";

  async function run() {
    setBusy(true); setError("");
    try {
      const icon = await iconPng(drawn.current?.querySelector("svg")).catch(() => undefined);
      setDone(await applicationRequest<Exported>(`/api/applications/${encodeURIComponent(row!.id)}/export`, "POST", { output: folder.trim(), icon }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }
  async function reveal() {
    try { await applicationRequest("/api/applications/exports/reveal", "POST", { path: done?.path }); onClose(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }
  async function browse() {
    try { const selected = await pickFolder(); if (selected) setFolder(selected); }
    catch (reason) { setError(String(reason)); }
  }

  return (
    <Dialog open onOpenChange={next => { if (!next && !busy) onClose(); }}>
      <DialogContent className="sm:max-w-[520px]">
        <div ref={drawn} hidden><ApplicationIcon icon={row.icon} id={row.id} size={1024} /></div>
        {done ? (
          <div className="grid gap-[10px]">
            <DialogHeader>
              <DialogTitle>{text(`Exported ${row.title}`, `已导出 ${row.title}`)}</DialogTitle>
              <DialogDescription>{text(
                `${name} (${Math.round(done.size / 1e6)} MB) is in ${where}. Double-click it to open.`,
                `${name}（${Math.round(done.size / 1e6)} MB）已保存到 ${where}，双击就能打开。`)}</DialogDescription>
            </DialogHeader>
            {error && <p role="alert" className={styles.dialogError}>{error}</p>}
            <DialogFooter>
              <Button type="button" variant="elevated" onClick={onClose}>{text("Done", "完成")}</Button>
              <Button type="button" onClick={() => void reveal()}>
                {isMac() ? text("Show in Finder", "在访达中显示") : text("Show in file manager", "在文件管理器中显示")}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form className="grid gap-[10px]" onSubmit={event => { event.preventDefault(); void run(); }}>
            <DialogHeader>
              <DialogTitle>{text(`Export ${row.title}`, `导出 ${row.title}`)}</DialogTitle>
              <DialogDescription>{text(
                "Makes an application that opens on its own, without OpenProgram open. It uses this computer's model and voice settings and carries no keys or data.",
                "做成一个可以单独打开的应用：不用打开 OpenProgram 也能用，模型和语音沿用这台电脑的设置，里面不带密钥和数据。")}</DialogDescription>
            </DialogHeader>
            <label className={styles.fieldLabel} htmlFor="application-export-folder">{text("Save to", "保存到")}</label>
            <div className={`${styles.directory} flex gap-[8px]`}>
              <Input id="application-export-folder" className="flex-1 max-[760px]:flex-none" value={folder}
                onChange={event => setFolder(event.target.value)} required disabled={busy} />
              <Button type="button" variant="elevated" disabled={busy} onClick={() => void browse()}>
                <FolderOpenIcon size={16} aria-hidden />{text("Browse", "浏览")}
              </Button>
            </div>
            <p className={styles.exportNote}>{busy
              ? text("Copying the runtime; this takes about half a minute", "正在复制运行环境，大约半分钟")
              : file}</p>
            {error && <p role="alert" className={styles.dialogError}>{error}</p>}
            <DialogFooter>
              <Button type="button" variant="elevated" disabled={busy} onClick={onClose}>{text("Cancel", "取消")}</Button>
              <Button type="submit" disabled={busy || !folder.trim()}>{busy ? text("Exporting…", "正在导出…") : text("Export", "导出")}</Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
