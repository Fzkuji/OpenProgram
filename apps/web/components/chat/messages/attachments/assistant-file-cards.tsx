"use client";
import { isLive } from "@/lib/chat/transcript/row-status";
import { useEffect, useMemo, useState, type ReactElement, type ReactNode } from "react";
import { ArrowUpRight, Download, Folder } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { useSessionStore, type ChatMsg } from "@/lib/session-store";
import { useCurrentProject } from "@/lib/files/files-shared";
import { replyFiles, inlineMediaKind, type FileOutput, type InlineMediaKind } from "@/lib/chat/transcript/file-outputs";
import { fileCapabilities, fileExtension } from "@/lib/documents/file-formats";
import { isHtmlPath } from "@/lib/files/local-file-url";
import { openLocalFile } from "@/lib/files/open-local-file";
import { formatBytes, isArchivePath } from "@/lib/files/archive";
import { canRevealFiles, revealAbsolutePath, revealLabel } from "@/lib/files/reveal-file";
import { showToast } from "@/lib/format-utils/toast";
import { useEffectEvent } from "@/lib/hooks/use-effect-event";
import { absRawFileUrl } from "@/lib/files/files-shared";
import { Button } from "@/components/ui/button";
import { FileTypeIcon } from "@/components/files/file-type-icon";
import { FilePathContextMenu, type FileMenuProject } from "@/components/files/file-path-menu";
import { MediaPlayer } from "@/components/files/preview/media-player";
import type { ParsedAttachment } from "./user-attachments";
import { ArchiveSummary, HtmlThumbnail, PdfFirstPage, TextHead, type PreviewInfo } from "./file-card-previews";
import styles from "./assistant-file-cards.module.css";

/** What a card shows above its name. */
type PreviewKind = InlineMediaKind | "pdf" | "page" | "archive" | "text" | null;

function previewKind(file: FileOutput): PreviewKind {
  const media = inlineMediaKind(file.path);
  if (media) return media;
  if (isArchivePath(file.path)) return "archive";
  if (isHtmlPath(file.path)) return "page";
  const preview = fileCapabilities(file.path).preview;
  return preview === "pdf" ? "pdf" : preview === "text" ? "text" : null;
}

/** A file the app can open: anything with a viewer, archives included. */
function canOpen(path: string): boolean {
  return fileCapabilities(path).preview !== "download" || isArchivePath(path);
}

/** The attachment meta ("mp4 · 3465 KB · …") as the card shows it: the
 *  format is left out when the name already ends in it, and the size is
 *  written in the largest unit that keeps it short (3.4 MB). */
export function cardMeta(meta: string, name: string): string {
  const ext = fileExtension(name).toLowerCase();
  return meta.split(" · ").flatMap(part => {
    if (ext && part.toLowerCase() === ext) return [];
    const kb = /^(\d+(?:\.\d+)?) KB$/.exec(part);
    return [kb ? formatBytes(Math.round(Number(kb[1]) * 1024)) : part];
  }).join(" · ");
}

/** One reply file: a preview of what it holds, then its name and the
 *  same two actions in the same place on every card — open it, and show
 *  it in the file manager. A file nothing here can show offers a download
 *  in the open slot; without a local file manager (a browser on another
 *  machine) the second slot downloads instead. */
function FileCard({ file, kind, meta, sessionId, project, open }: { file: FileOutput; kind: PreviewKind; meta: string; sessionId?: string; project?: FileMenuProject | null; open(): void }) {
  const { text } = useTranslation();
  const [info, setInfo] = useState<PreviewInfo>({});
  const [mediaFailed, setMediaFailed] = useState(false);
  const openable = canOpen(file.path) && !!sessionId;
  const src = absRawFileUrl(file.path, sessionId);
  const shown = mediaFailed ? null : kind;
  const details = [cardMeta(meta, file.name),
    info.pages ? text(`${info.pages} pages`, `${info.pages} 页`) : "",
    info.files !== undefined ? text(`${info.files} files`, `${info.files} 个文件`) : ""].filter(Boolean).join(" · ");
  const download = <Button asChild variant="elevated" size="xs"><a href={src} download={file.name}><Download aria-hidden />{text("Download", "下载")}</a></Button>;
  const reveal = async () => {
    if (!(await revealAbsolutePath(file.path, sessionId))) showToast(text("Couldn't show this file.", "无法显示该文件。"), { tone: "error" });
  };
  let preview: ReactNode = null;
  if (sessionId && shown === "image") preview = <img src={src} alt={file.name} onError={() => setMediaFailed(true)} />;
  else if (sessionId && shown === "video") preview = <MediaPlayer kind="video" src={src} label={file.name} onError={() => setMediaFailed(true)} />;
  else if (sessionId && shown === "audio") preview = <MediaPlayer kind="audio" src={src} label={file.name} onError={() => setMediaFailed(true)} />;
  else if (sessionId && shown === "pdf") preview = <PdfFirstPage path={file.path} sessionId={sessionId} onInfo={setInfo} />;
  else if (sessionId && shown === "page") preview = <HtmlThumbnail path={file.path} sessionId={sessionId} />;
  else if (sessionId && shown === "archive") preview = <ArchiveSummary path={file.path} sessionId={sessionId} onInfo={setInfo} onOpen={open} />;
  else if (sessionId && shown === "text") preview = <TextHead path={file.path} sessionId={sessionId} />;
  // Players keep their own controls; every other preview opens the file.
  const clickable = shown !== "video" && shown !== "audio" && shown !== "archive" && openable;
  const icon = <FileTypeIcon name={file.name} size={preview && shown !== "audio" ? 16 : 20} />;
  const actions = <span className={`${styles.actions} flex shrink-0 items-center gap-[6px]`}>
    {openable ? <Button type="button" variant="elevated" size="xs" onClick={open}><ArrowUpRight aria-hidden />{text("Open", "打开")}</Button> : download}
    {canRevealFiles()
      ? <Button type="button" variant="elevated" size="xs" onClick={() => void reveal()}><Folder aria-hidden />{revealLabel(text)}</Button>
      : openable && download}
  </span>;
  // A preview stays clean: the name is one quiet line under the card and
  // the two actions float on the preview's top right while the pointer is
  // on it, so the card is no taller than what it shows. A player-only card
  // (audio) or a card with nothing to show keeps the name row inside.
  const overlay = !!preview && shown !== "audio";
  const previewBox = preview && (clickable
    ? <button type="button" className={styles.preview} onClick={open} aria-label={`${file.name} ${text("Open", "打开")}`}>{preview}</button>
    : <div className={styles.preview}>{preview}</div>);
  const media = shown === "image" || shown === "video" || shown === "audio" ? "" : undefined;
  // Right-click: the file menu every chat file shares (file-path-menu.tsx).
  const menu = (card: ReactElement) => <FilePathContextMenu path={file.path} sessionId={sessionId} project={project} onOpen={openable ? open : undefined}>{card}</FilePathContextMenu>;
  if (overlay) return menu(<figure className={`${styles.shown} m-0 grid w-full min-w-0 gap-[6px]`} data-kind={shown ?? "file"} data-media={media}>
    <div className={styles.frame}>
      {previewBox}
      <span className={styles.floating}>{actions}</span>
    </div>
    <figcaption className={styles.caption}>
      {icon}
      <strong className={styles.captionName}>{file.name}</strong>
      {details && <span className={styles.captionMeta}>{details}</span>}
    </figcaption>
  </figure>);
  return menu(<figure className={styles.card} data-kind={shown ?? "file"} data-media={media}>
    {previewBox}
    <figcaption className={`${styles.footer} @max-[460px]:flex-wrap @max-[460px]:gap-y-[8px]`}>
      <span className={styles.icon}>{icon}</span>
      <span className={`${styles.label} grid min-w-0 flex-1 gap-[3px]`}><strong>{file.name}</strong>{details && <small>{details}</small>}</span>
      {actions}
    </figcaption>
  </figure>);
}

/** A file ``send_file`` handed back, matched to the path the call asked
 *  for (absolute, or relative to where the agent ran). */
export function sentFilePath(input: string | undefined, attachments: ParsedAttachment[]): string | null {
  let asked = "";
  try { asked = String((JSON.parse(input || "{}") as { path?: unknown }).path ?? ""); } catch { return null; }
  if (!asked) return null;
  const relative = asked.replace(/\\/g, "/").replace(/^\.\//, "");
  const hit = attachments.find(item => item.path === asked)
    ?? attachments.find(item => item.path.replace(/\\/g, "/").endsWith("/" + relative));
  return hit?.path ?? null;
}

/** The reply's files: the attachments the server put on it, never paths
 *  linked from the prose (those stay links). With ``only`` it draws just
 *  those, where the ``send_file`` call that sent them sits in the reply;
 *  the end-of-reply list passes ``skip`` so a file is drawn once, and only
 *  it opens the first deliverable automatically when the turn just
 *  finished. */
export function AssistantFileCards({ msg, sessionId, attachments, only, skip }: { msg: ChatMsg; sessionId?: string; attachments: ParsedAttachment[]; only?: string[]; skip?: string[] }) {
  const focusedSessionId = useSessionStore(state => state.currentSessionId);
  const project = useCurrentProject({ sessionId: sessionId ?? null, chatKey: null });
  const allFiles = useMemo(() => replyFiles(attachments), [attachments]);
  const files = useMemo(() => allFiles.filter(file => only ? only.includes(file.path) : !skip?.includes(file.path)), [allFiles, only, skip]);
  // A web page opens as a page; its source stays one click away there.
  const open = (file: FileOutput, automatic = false) => !!sessionId && openLocalFile(file.path, sessionId, project, automatic);
  const openAutomatically = useEffectEvent((file: FileOutput) => open(file, true));
  useEffect(() => {
    if (only || !sessionId || !msg.autoPreviewFiles || project === undefined || isLive(msg.status)) return;
    // Consume before attempting: switching back, hydration and remount cannot replay the request.
    useSessionStore.getState().applyLocalChange(sessionId, { kind: "local_patch", rowId: msg.id, fields: { autoPreviewFiles: false } });
    if (sessionId !== focusedSessionId || msg.status !== "done") return;
    // Media already plays in the reply; only open what is not shown here.
    const file = allFiles.find(item => fileCapabilities(item.path).preview !== "download" && !inlineMediaKind(item.path));
    if (file) openAutomatically(file);
  }, [only, sessionId, focusedSessionId, msg.id, msg.status, msg.autoPreviewFiles, allFiles, project]);
  if (!files.length) return null;
  const meta = (path: string) => attachments.find(item => item.path === path)?.meta ?? "";
  return <div className="grid gap-[8px]" data-assistant-files>{files.map(file =>
    <FileCard key={file.path} file={file} kind={sessionId ? previewKind(file) : null} meta={meta(file.path)} sessionId={sessionId} project={project} open={() => void open(file)} />,
  )}</div>;
}
