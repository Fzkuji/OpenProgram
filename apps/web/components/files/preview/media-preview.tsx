"use client";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "@/lib/i18n";

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
  const props = {
    controls: true, preload: "metadata", src: sourceUrl,
    onError: () => setFailed(true),
    "aria-label": path.split("/").pop(),
    style: { maxWidth: "100%", maxHeight: "100%" },
  };
  return <div style={{ padding: 16, height: "100%", overflow: "auto" }}>
    {kind === "audio"
      ? <audio key={sourceUrl} ref={(node) => { media.current = node; }} {...props} />
      : <video key={sourceUrl} ref={(node) => { media.current = node; }} {...props} />}
    {failed && <p role="alert">{text(
      "This file could not be played. It may be damaged or use a codec this browser does not support.",
      "无法播放此文件。文件可能已损坏，或浏览器不支持它使用的编解码器。",
    )}</p>}
    <p><a href={sourceUrl} download={path.split("/").pop()}>{text("Download original", "下载原文件")}</a></p>
  </div>;
}
