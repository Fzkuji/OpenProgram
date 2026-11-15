"use client";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "@/lib/i18n";
import { MediaPlayer } from "./media-player";
import styles from "./media-player.module.css";

/** The source URL is already scoped by FileViewer, including retained versions. */
export function MediaPreview({ sourceUrl, path, kind }: {
  sourceUrl: string; path: string; kind: "audio" | "video";
}) {
  const { text } = useTranslation();
  const media = useRef<HTMLMediaElement | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setFailed(false);
    const element = media.current;
    if (!element) return;
    // Stop network reads and playback when the file/version changes or closes.
    return () => { element.pause(); element.removeAttribute("src"); element.load(); };
  }, [sourceUrl, kind]);
  const name = path.split("/").pop() || path;
  return <div style={{ padding: 16, height: "100%", overflow: "auto", boxSizing: "border-box" }}>
    {/* One clip path rounds the picture and the player's bar along the
        same curve; a square video's light corners read as a frame error
        against the panel's curved surfaces. */}
    <MediaPlayer key={sourceUrl} kind={kind} ref={(node) => { media.current = node; }} src={sourceUrl}
      label={name} onError={() => setFailed(true)}
      className={kind === "video" ? styles.video : styles.audio}
      style={{ maxHeight: "calc(100vh - 160px)" }} />
    {failed && <p role="alert">{text(
      "This file could not be played. It may be damaged or use a codec this browser does not support.",
      "无法播放此文件。文件可能已损坏，或浏览器不支持它使用的编解码器。",
    )}</p>}
    <p><a href={sourceUrl} download={path.split("/").pop()}>{text("Download original", "下载原文件")}</a></p>
  </div>;
}
