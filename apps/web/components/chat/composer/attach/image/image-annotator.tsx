"use client";

/**
 * Draw on a staged image before it is sent, with the screenshot tool's
 * marks (components/annotate): rectangle, ellipse, arrow, pen, highlighter,
 * mosaic, text and step numbers, in seven colours and three widths, with
 * undo and redo. Marks are kept in image pixels, so the saved PNG has the
 * image's own size.
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { AnnotateToolbar, TOOL_KEYS } from "@/components/annotate/annotate-toolbar";
import { Button } from "@/components/ui/button";
import {
  COLORS, WIDTHS, commit, drawMarks, emptyHistory, isEmpty, nextNumber, redo, textSize, undo,
  type History, type Mark, type Point, type Tool,
} from "@/lib/annotate/marks";
import { useTranslation } from "@/lib/i18n";
import styles from "./image-annotator.module.css";

/** Image pixels per CSS px a mark is drawn at: a large screenshot gets proportionally thicker marks. */
function markScale(image: HTMLImageElement): number {
  return Math.max(1, Math.max(image.naturalWidth, image.naturalHeight) / 1200);
}

export function ImageAnnotator({ src, filename, onCancel, onSave }: {
  src: string;
  filename: string;
  onCancel: () => void;
  onSave: (file: File) => void;
}) {
  const { text } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [history, setHistory] = useState<History>(emptyHistory);
  const [draft, setDraft] = useState<Mark | null>(null);
  const [tool, setTool] = useState<Tool | null>("rect");
  const [color, setColor] = useState(COLORS[0]);
  const [width, setWidth] = useState(WIDTHS[1]);
  const [typing, setTyping] = useState<{ at: Point; screen: Point; value: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const marks = history.present;
  const scale = image ? markScale(image) : 1;

  useEffect(() => {
    const img = new Image();
    img.onload = () => setImage(img);
    img.src = src;
  }, [src]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !image) return;
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
  }, [image]);

  useEffect(() => {
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx || !image) return;
    ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
    ctx.drawImage(image, 0, 0);
    drawMarks(ctx, draft ? [...marks, draft] : marks, { scale, source: image });
  }, [image, marks, draft, scale]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (typing) return;
      const mod = event.metaKey || event.ctrlKey;
      if (event.key === "Escape") { event.stopPropagation(); onCancel(); return; }
      if (mod && event.key.toLowerCase() === "z") {
        event.preventDefault();
        setHistory((h) => (event.shiftKey ? redo(h) : undo(h)));
        return;
      }
      if (mod && event.key.toLowerCase() === "y") { event.preventDefault(); setHistory(redo); return; }
      const picked = TOOL_KEYS[event.key.toLowerCase()];
      if (!mod && picked) setTool(picked);
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onCancel, typing]);

  function toImage(event: React.PointerEvent): Point {
    const canvas = canvasRef.current!;
    const box = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - box.left) / box.width) * canvas.width,
      y: ((event.clientY - box.top) / box.height) * canvas.height,
    };
  }

  function commitText() {
    if (typing?.value.trim()) {
      const mark: Mark = { kind: "text", color, width, at: typing.at, text: typing.value.trim() };
      setHistory((h) => commit(h, [...h.present, mark]));
    }
    setTyping(null);
  }

  function onPointerDown(event: React.PointerEvent<HTMLCanvasElement>) {
    if (!image || !tool) return;
    const at = toImage(event);
    if (tool === "text") {
      commitText();
      const box = event.currentTarget.parentElement!.getBoundingClientRect();
      setTyping({ at, screen: { x: event.clientX - box.left, y: event.clientY - box.top }, value: "" });
      return;
    }
    if (tool === "number") {
      setHistory((h) => commit(h, [...h.present, { kind: "number", color, width, at, n: nextNumber(h.present) }]));
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    setDraft(tool === "pen" || tool === "highlighter"
      ? { kind: tool, color, width, points: [at] }
      : { kind: tool, color, width, from: at, to: at });
  }

  function onPointerMove(event: React.PointerEvent<HTMLCanvasElement>) {
    if (!draft) return;
    const at = toImage(event);
    if ("points" in draft) setDraft({ ...draft, points: [...draft.points, at] });
    else if ("from" in draft) setDraft({ ...draft, to: at });
  }

  function onPointerUp() {
    if (draft && !isEmpty(draft)) setHistory((h) => commit(h, [...h.present, draft]));
    setDraft(null);
  }

  function save() {
    if (!image || saving) return;
    // Draw the final marks, an unfinished text included, on a fresh canvas.
    const pending: Mark[] = typing?.value.trim()
      ? [{ kind: "text", color, width, at: typing.at, text: typing.value.trim() }] : [];
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(image, 0, 0);
    drawMarks(ctx, [...marks, ...pending], { scale, source: image });
    setSaving(true);
    canvas.toBlob((blob) => {
      setSaving(false);
      if (!blob) return;
      const base = filename.replace(/\.[^.]+$/, "") || "image";
      onSave(new File([blob], `${base}.png`, { type: "image/png" }));
    }, "image/png");
  }

  if (typeof document === "undefined") return null;
  const displayScale = (() => {
    const canvas = canvasRef.current;
    return canvas ? canvas.getBoundingClientRect().width / Math.max(1, canvas.width) : 1;
  })();
  return createPortal(
    // Rendered from inside the preview, whose backdrop closes on click; a
    // portal still bubbles React events to it.
    <div className={styles.overlay} data-native-view-occluder="true" role="dialog" aria-modal="true"
      aria-label={text("Annotate image", "标注图片")} onClick={(event) => event.stopPropagation()}>
      <div className={styles.toolbar}>
        <AnnotateToolbar
          layout="inline"
          tool={tool} onToolChange={(next) => { commitText(); setTool(next); }}
          color={color} onColorChange={setColor} width={width} onWidthChange={setWidth}
          canUndo={history.past.length > 0} canRedo={history.future.length > 0}
          onUndo={() => setHistory(undo)} onRedo={() => setHistory(redo)}
        />
        <span className={styles.spacer} />
        <Button variant="elevated" size="xs" onClick={onCancel}>{text("Cancel", "取消")}</Button>
        <Button size="xs" disabled={!image || saving || (!marks.length && !typing?.value.trim())}
          onClick={save}>
          {text("Done", "完成")}
        </Button>
      </div>
      <div className={styles.stage}>
        <div className={styles.frame}>
          <canvas ref={canvasRef} className={styles.canvas} data-tool={tool ?? undefined}
            onPointerDown={onPointerDown} onPointerMove={onPointerMove}
            onPointerUp={onPointerUp} onPointerCancel={onPointerUp} />
          {typing && (
            <input autoFocus className={styles.textInput}
              style={{ left: typing.screen.x, top: typing.screen.y, color, fontSize: textSize(width) * scale * displayScale }}
              value={typing.value} aria-label={text("Annotation text", "标注文字")}
              onChange={(event) => setTyping({ ...typing, value: event.target.value })}
              onKeyDown={(event) => {
                if (event.key === "Enter") { event.preventDefault(); commitText(); }
                if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setTyping(null); }
              }}
              onBlur={commitText} />
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
