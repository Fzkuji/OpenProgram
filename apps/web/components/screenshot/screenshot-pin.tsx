"use client";

/** The picture inside a pinned screenshot's window; see app/menu-overlay/screenshot-pin. */
import { useEffect, useState } from "react";

import { desktopBridge } from "@/lib/desktop/bridge/bridge-api";
import { useTranslation } from "@/lib/i18n";
import styles from "./screenshot-pin.module.css";

export function ScreenshotPin() {
  const { text } = useTranslation();
  const bridge = desktopBridge()?.screenshot;
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let url: string | null = null;
    void bridge?.pinImage().then((image) => {
      if (!image) return;
      url = URL.createObjectURL(new Blob([image.png as BlobPart], { type: "image/png" }));
      setSrc(url);
    });
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [bridge]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") bridge?.pinClose(); };
    const end = () => bridge?.pinDrag("end");
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerup", end);
    window.addEventListener("blur", end);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("blur", end);
    };
  }, [bridge]);

  if (!bridge || !src) return null;
  return (
    <img src={src} alt="" draggable={false} className={styles.image}
      onPointerDown={(event) => { if (event.button === 0) bridge.pinDrag("start"); }}
      onPointerUp={() => bridge.pinDrag("end")}
      onDoubleClick={() => bridge.pinClose()}
      onWheel={(event) => bridge.pinWheel({ deltaY: event.deltaY, opacity: event.metaKey || event.ctrlKey })}
      onContextMenu={(event) => {
        event.preventDefault();
        bridge.pinMenu({
          copy: text("Copy", "复制"),
          save: text("Save…", "保存…"),
          actualSize: text("Actual size", "原始大小"),
          close: text("Close", "关闭"),
        });
      }} />
  );
}
