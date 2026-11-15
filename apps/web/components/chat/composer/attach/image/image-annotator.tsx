"use client";

/**
 * Draw on a staged image before it is sent, as Claude's composer does:
 * pen, rectangle, arrow and text in a few colours, with undo. Marks are
 * kept in image pixels, so the saved PNG has the image's own size.
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowUpRight, Pen, Square, Type, Undo2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useTranslation } from "@/lib/i18n";
import styles from "./image-annotator.module.css";

type Point = { x: number; y: number };
type Tool = "pen" | "rect" | "arrow" | "text";
type Mark =
  | { kind: "pen"; color: string; points: Point[] }
  | { kind: "rect" | "arrow"; color: string; from: Point; to: Point }
  | { kind: "text"; color: string; at: Point; text: string };

const COLORS = ["#ef4444", "#f97316", "#facc15", "#22c55e", "#3b82f6", "#ffffff", "#111111"];

function drawMarks(ctx: CanvasRenderingContext2D, image: HTMLImageElement, marks: Mark[]) {
  const { width, height } = ctx.canvas;
  const line = Math.max(2, Math.round(Math.max(width, height) / 300));
  ctx.clearRect(0, 0, width, height);
  ctx.drawImage(image, 0, 0, width, height);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.lineWidth = line;
  for (const mark of marks) {
    ctx.strokeStyle = mark.color;
    ctx.fillStyle = mark.color;
    if (mark.kind === "pen") {
      ctx.beginPath();
      mark.points.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.stroke();
    } else if (mark.kind === "rect") {
      ctx.strokeRect(mark.from.x, mark.from.y, mark.to.x - mark.from.x, mark.to.y - mark.from.y);
    } else if (mark.kind === "arrow") {
      const angle = Math.atan2(mark.to.y - mark.from.y, mark.to.x - mark.from.x);
      const head = line * 5;
      ctx.beginPath();
      ctx.moveTo(mark.from.x, mark.from.y);
      ctx.lineTo(mark.to.x, mark.to.y);
      ctx.moveTo(mark.to.x - head * Math.cos(angle - Math.PI / 6), mark.to.y - head * Math.sin(angle - Math.PI / 6));
      ctx.lineTo(mark.to.x, mark.to.y);
      ctx.lineTo(mark.to.x - head * Math.cos(angle + Math.PI / 6), mark.to.y - head * Math.sin(angle + Math.PI / 6));
      ctx.stroke();
    } else if (mark.kind === "text") {
      ctx.font = `600 ${line * 8}px ${getComputedStyle(document.body).fontFamily}`;
      ctx.textBaseline = "top";
      ctx.fillText(mark.text, mark.at.x, mark.at.y);
    }
  }
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
  const [marks, setMarks] = useState<Mark[]>([]);
  const [draft, setDraft] = useState<Mark | null>(null);
  const [tool, setTool] = useState<Tool>("pen");
  const [color, setColor] = useState(COLORS[0]);
  const [typing, setTyping] = useState<{ at: Point; screen: Point; value: string } | null>(null);
  const [saving, setSaving] = useState(false);

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
    if (ctx && image) drawMarks(ctx, image, draft ? [...marks, draft] : marks);
  }, [image, marks, draft]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (typing) return;
      if (event.key === "Escape") { event.stopPropagation(); onCancel(); }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        setMarks((list) => list.slice(0, -1));
      }
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
    if (typing?.value.trim()) setMarks((list) => [...list, { kind: "text", color, at: typing.at, text: typing.value.trim() }]);
    setTyping(null);
  }

  function onPointerDown(event: React.PointerEvent<HTMLCanvasElement>) {
    if (!image) return;
    const at = toImage(event);
    if (tool === "text") {
      commitText();
      const box = event.currentTarget.parentElement!.getBoundingClientRect();
      setTyping({ at, screen: { x: event.clientX - box.left, y: event.clientY - box.top }, value: "" });
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    setDraft(tool === "pen" ? { kind: "pen", color, points: [at] } : { kind: tool, color, from: at, to: at });
  }

  function onPointerMove(event: React.PointerEvent<HTMLCanvasElement>) {
    if (!draft) return;
    const at = toImage(event);
    setDraft(draft.kind === "pen" ? { ...draft, points: [...draft.points, at] }
      : draft.kind === "text" ? draft : { ...draft, to: at });
  }

  function onPointerUp() {
    if (draft) setMarks((list) => [...list, draft]);
    setDraft(null);
  }

  function save() {
    if (!image || saving) return;
    // Draw the final marks, an unfinished text included, on a fresh canvas.
    const pending: Mark[] = typing?.value.trim()
      ? [{ kind: "text", color, at: typing.at, text: typing.value.trim() }] : [];
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    drawMarks(ctx, image, [...marks, ...pending]);
    setSaving(true);
    canvas.toBlob((blob) => {
      setSaving(false);
      if (!blob) return;
      const base = filename.replace(/\.[^.]+$/, "") || "image";
      onSave(new File([blob], `${base}.png`, { type: "image/png" }));
    }, "image/png");
  }

  if (typeof document === "undefined") return null;
  return createPortal(
    // Rendered from inside the preview, whose backdrop closes on click; a
    // portal still bubbles React events to it.
    <div className={styles.overlay} data-native-view-occluder="true" role="dialog" aria-modal="true"
      aria-label={text("Annotate image", "标注图片")} onClick={(event) => event.stopPropagation()}>
      <div className={styles.toolbar}>
        <ToggleGroup type="single" variant="elevated" size="sm" value={tool}
          onValueChange={(value) => { if (value) { commitText(); setTool(value as Tool); } }}
          aria-label={text("Drawing tool", "绘制工具")}>
          <ToggleGroupItem value="pen" aria-label={text("Pen", "画笔")}><Pen /></ToggleGroupItem>
          <ToggleGroupItem value="rect" aria-label={text("Rectangle", "矩形")}><Square /></ToggleGroupItem>
          <ToggleGroupItem value="arrow" aria-label={text("Arrow", "箭头")}><ArrowUpRight /></ToggleGroupItem>
          <ToggleGroupItem value="text" aria-label={text("Text", "文字")}><Type /></ToggleGroupItem>
        </ToggleGroup>
        <div className={styles.colors} role="radiogroup" aria-label={text("Colour", "颜色")}>
          {COLORS.map((swatch) => (
            <button key={swatch} type="button" role="radio" aria-checked={color === swatch}
              aria-label={swatch} className={styles.swatch} style={{ background: swatch }}
              onClick={() => setColor(swatch)} />
          ))}
        </div>
        <Button variant="elevated" size="icon-xs" disabled={!marks.length}
          onClick={() => setMarks((list) => list.slice(0, -1))} aria-label={text("Undo", "撤销")}>
          <Undo2 />
        </Button>
        <span className={styles.spacer} />
        <Button variant="elevated" size="xs" onClick={onCancel}>{text("Cancel", "取消")}</Button>
        <Button size="xs" disabled={!image || saving || (!marks.length && !typing?.value.trim())}
          onClick={save}>
          {text("Done", "完成")}
        </Button>
      </div>
      <div className={styles.stage}>
        <div className={styles.frame}>
          <canvas ref={canvasRef} className={styles.canvas} data-tool={tool}
            onPointerDown={onPointerDown} onPointerMove={onPointerMove}
            onPointerUp={onPointerUp} onPointerCancel={onPointerUp} />
          {typing && (
            <input autoFocus className={styles.textInput} style={{ left: typing.screen.x, top: typing.screen.y, color }}
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
