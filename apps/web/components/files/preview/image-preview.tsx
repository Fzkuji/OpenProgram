"use client";
import { useState } from "react";
import { useTranslation } from "@/lib/i18n";

/** SVG remains an image resource, never document-controlled inline markup. */
export function ImagePreview({ sourceUrl, path }: { sourceUrl: string; path: string }) {
  const { text } = useTranslation();
  const [failed, setFailed] = useState(false);
  return <div style={{ padding: 16, height: "100%", overflow: "auto" }}>
    {!failed && <img src={sourceUrl} alt={path} style={{ maxWidth: "100%" }} onError={() => setFailed(true)} />}
    {failed && <p role="alert">{text("This image could not be displayed. It may be damaged or unsupported.",
      "无法显示此图片。文件可能已损坏，或格式不受支持。")}</p>}
    <p><a href={sourceUrl} download={path.split("/").pop()}>{text("Download original", "下载原文件")}</a></p>
  </div>;
}
