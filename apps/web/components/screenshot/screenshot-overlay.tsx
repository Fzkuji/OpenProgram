"use client";

/**
 * The screenshot overlay: one per display, over everything, showing the
 * display frozen at the moment of the shortcut (apps/desktop/main/windows/
 * screenshot.js captures it). As in Snow Shot:
 *
 * - Pointing at a window outlines it; a click takes it, a drag takes any
 *   region. A magnifier shows the pixels, the position and the colour under
 *   the pointer (C copies the colour).
 * - The selection keeps eight handles and moves when dragged; the tools bar
 *   sits under it, above it when there is no room below, inside it when
 *   there is room on neither side.
 * - Marks are drawn on the selection; copy, save, pin to the screen and
 *   send to the conversation take the selection with its marks; text
 *   recognition reads the selection without them.
 *
 * Every position here is in the overlay's CSS px (the display's points);
 * the picture and the marks are in its pixels, `scale` per CSS px.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { ActionButton, AnnotateToolbar, TOOL_KEYS } from "@/components/annotate/annotate-toolbar";
import { Button } from "@/components/ui/button";
import {
  commit, drawMarks, emptyHistory, isEmpty, nextNumber, normalized, redo, undo,
  WIDTHS, COLORS, type History, type Mark, type Point, type Tool,
} from "@/lib/annotate/marks";
import { desktopBridge } from "@/lib/desktop/bridge/bridge-api";
import type { ScreenshotFrame, ScreenshotWindow, ScreenshotAction } from "@/lib/desktop/bridge/bridge-api";
import { useTranslation } from "@/lib/i18n";
import {
  clampRect, handleAt, moveRect, placeBeside, placeToolbar, resizeRect, type Handle, type Rect,
} from "@/lib/screenshot/geometry";
import styles from "./screenshot-overlay.module.css";

type Drag =
  | { kind: "create"; from: Point }
  | { kind: "move"; from: Point; start: Rect }
  | { kind: "resize"; handle: Handle; start: Rect; from: Point }
  | { kind: "draw" };

type OcrLine = { text: string; box: [number, number, number, number] };

const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
const MAGNIFIER_PIXELS = 15;
const MAGNIFIER_SIZE = 120;
const DRAG_THRESHOLD = 3;

function toHex(r: number, g: number, b: number) {
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("").toUpperCase()}`;
}

/** BGRA rows into an RGBA ImageData. */
function imageData(frame: ScreenshotFrame): ImageData {
  const source = frame.pixels;
  const out = new Uint8ClampedArray(frame.width * frame.height * 4);
  const stride = source.length / frame.height;
  for (let y = 0; y < frame.height; y++) {
    let s = y * stride;
    let d = y * frame.width * 4;
    for (let x = 0; x < frame.width; x++, s += 4, d += 4) {
      out[d] = source[s + 2];
      out[d + 1] = source[s + 1];
      out[d + 2] = source[s];
      out[d + 3] = 255;
    }
  }
  return new ImageData(out, frame.width, frame.height);
}

async function canvasPng(canvas: HTMLCanvasElement): Promise<Uint8Array | null> {
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
  return blob ? new Uint8Array(await blob.arrayBuffer()) : null;
}

export function ScreenshotOverlay() {
  const { text } = useTranslation();
  const bridge = desktopBridge()?.screenshot;
  const baseRef = useRef<HTMLCanvasElement>(null);
  const marksRef = useRef<HTMLCanvasElement>(null);
  const magnifierRef = useRef<HTMLCanvasElement>(null);
  const ocrRef = useRef<HTMLDivElement>(null);

  const [frame, setFrame] = useState<ScreenshotFrame | null>(null);
  const [view, setView] = useState({ width: 0, height: 0 });
  const [windows, setWindows] = useState<ScreenshotWindow[]>([]);
  const [pointer, setPointer] = useState<Point | null>(null);
  const [selection, setSelection] = useState<Rect | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [released, setReleased] = useState(false);
  const [tool, setTool] = useState<Tool | null>(null);
  const [color, setColor] = useState(COLORS[0]);
  const [width, setWidth] = useState(WIDTHS[1]);
  const [history, setHistory] = useState<History>(emptyHistory);
  const [draft, setDraft] = useState<Mark | null>(null);
  const [typing, setTyping] = useState<{ at: Point; css: Point; value: string } | null>(null);
  const [ocr, setOcr] = useState<{ lines: OcrLine[]; rect: Rect } | null>(null);
  const [toolbarSize, setToolbarSize] = useState({ width: 0, height: 0 });
  const [ocrSize, setOcrSize] = useState({ width: 0, height: 0 });
  const [finishing, setFinishing] = useState(false);

  const scale = frame?.scale ?? 1;
  const mac = frame?.platform === "darwin";
  const screenRect = useMemo<Rect>(() => ({ x: 0, y: 0, width: view.width, height: view.height }), [view]);

  // -- the frame
  const reset = useCallback(() => {
    setSelection(null);
    setDrag(null);
    setReleased(false);
    setTool(null);
    setHistory(emptyHistory);
    setDraft(null);
    setTyping(null);
    setOcr(null);
    setFinishing(false);
    setWindows([]);
  }, []);

  const load = useCallback(async () => {
    if (!bridge) return;
    const next = await bridge.frame();
    if (!next) return;
    reset();
    setFrame(next);
    setPointer(next.cursor);
    void bridge.windows().then(setWindows).catch(() => setWindows([]));
  }, [bridge, reset]);

  useEffect(() => {
    if (!bridge) return;
    void load();
    const offs = [
      bridge.onBegin(() => { void load(); }),
      bridge.onEnd(() => { reset(); setFrame(null); }),
      bridge.onRelease(() => { setSelection(null); setDrag(null); setReleased(true); setOcr(null); setTyping(null); }),
    ];
    return () => offs.forEach((off) => off());
  }, [bridge, load, reset]);

  useLayoutEffect(() => {
    const measure = () => setView({ width: window.innerWidth, height: window.innerHeight });
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  // Draw the picture, then tell the main process the overlay can be shown.
  useEffect(() => {
    const canvas = baseRef.current;
    if (!canvas || !frame) return;
    canvas.width = frame.width;
    canvas.height = frame.height;
    const marks = marksRef.current;
    if (marks) { marks.width = frame.width; marks.height = frame.height; }
    canvas.getContext("2d")?.putImageData(imageData(frame), 0, 0);
    requestAnimationFrame(() => bridge?.painted(frame.started));
  }, [frame, bridge]);

  const marks = history.present;
  useEffect(() => {
    const canvas = marksRef.current;
    const base = baseRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !base || !ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!selection) return;
    ctx.save();
    ctx.beginPath();
    ctx.rect(selection.x * scale, selection.y * scale, selection.width * scale, selection.height * scale);
    ctx.clip();
    drawMarks(ctx, draft ? [...marks, draft] : marks, { scale, source: base });
    ctx.restore();
  }, [marks, draft, selection, scale, frame]);

  // -- geometry
  const hovered = useMemo<Rect | null>(() => {
    if (!pointer || selection || drag || released) return null;
    const hit = windows.find(({ rect }) => pointer.x >= rect.x && pointer.y >= rect.y
      && pointer.x < rect.x + rect.width && pointer.y < rect.y + rect.height);
    return hit ? clampRect(hit.rect, screenRect) : screenRect;
  }, [pointer, windows, selection, drag, released, screenRect]);

  const toolbarPlace = useMemo(
    () => (selection ? placeToolbar(selection, toolbarSize, screenRect) : null),
    [selection, toolbarSize, screenRect],
  );

  // Measured whenever the bar appears or changes size (the colours row comes and goes).
  const [toolbarNode, setToolbarNode] = useState<HTMLDivElement | null>(null);
  useLayoutEffect(() => {
    if (!toolbarNode) return;
    const measure = () => setToolbarSize({ width: toolbarNode.offsetWidth, height: toolbarNode.offsetHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(toolbarNode);
    return () => observer.disconnect();
  }, [toolbarNode]);

  useLayoutEffect(() => {
    const node = ocrRef.current;
    if (!node) return;
    setOcrSize({ width: node.offsetWidth, height: node.offsetHeight });
  }, [ocr]);

  // -- finishing
  const crop = useCallback((withMarks: boolean) => {
    const base = baseRef.current;
    if (!base || !selection) return null;
    const x = Math.round(selection.x * scale);
    const y = Math.round(selection.y * scale);
    const w = Math.max(1, Math.round(selection.width * scale));
    const h = Math.max(1, Math.round(selection.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(base, x, y, w, h, 0, 0, w, h);
    if (withMarks) {
      const pending: Mark[] = typing?.value.trim()
        ? [{ kind: "text", color, width, at: typing.at, text: typing.value.trim() }] : [];
      drawMarks(ctx, [...marks, ...pending], { scale, source: base, offset: { x, y } });
    }
    return canvas;
  }, [selection, scale, marks, typing, color, width]);

  const finish = useCallback(async (action: ScreenshotAction) => {
    if (!bridge || finishing) return;
    if (action === "cancel" || !selection) {
      setFinishing(true);
      await bridge.finish({ action: "cancel" });
      return;
    }
    const canvas = crop(true);
    if (!canvas) return;
    setFinishing(true);
    const png = await canvasPng(canvas);
    if (!png) { setFinishing(false); return; }
    await bridge.finish({ action, png, rect: selection });
  }, [bridge, finishing, selection, crop]);

  const recognize = useCallback(async () => {
    if (ocr) { setOcr(null); return; }
    const canvas = crop(false);
    if (!canvas || !selection) return;
    const png = await canvasPng(canvas);
    if (!png) return;
    try {
      const response = await fetch("/api/screenshot/ocr", {
        method: "POST", headers: { "Content-Type": "image/png" }, body: png as BodyInit,
      });
      const body = response.ok ? await response.json() as { lines?: OcrLine[] } : { lines: [] };
      setOcr({ lines: body.lines ?? [], rect: selection });
    } catch {
      setOcr({ lines: [], rect: selection });
    }
  }, [ocr, crop, selection]);

  const ocrText = useMemo(() => (ocr?.lines ?? []).map((line) => line.text).join("\n"), [ocr]);

  // -- text marks
  const commitText = useCallback(() => {
    setTyping((current) => {
      if (current?.value.trim()) {
        const mark: Mark = { kind: "text", color, width, at: current.at, text: current.value.trim() };
        setHistory((h) => commit(h, [...h.present, mark]));
      }
      return null;
    });
  }, [color, width]);

  // -- pointer
  const at = (event: React.PointerEvent | React.MouseEvent): Point => ({ x: event.clientX, y: event.clientY });
  const toImage = (p: Point): Point => ({ x: p.x * scale, y: p.y * scale });
  const inside = (p: Point, r: Rect | null) => Boolean(r && p.x >= r.x && p.y >= r.y && p.x <= r.x + r.width && p.y <= r.y + r.height);

  function onPointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (finishing || !frame) return;
    if (event.button === 2) return;
    if ((event.target as HTMLElement).closest("[data-screenshot-ui]")) return;
    const p = at(event);
    event.currentTarget.setPointerCapture(event.pointerId);
    if (typing) commitText();
    if (selection) {
      const handle = handleAt(p, selection, 6);
      if (handle) { setDrag({ kind: "resize", handle, start: selection, from: p }); return; }
      if (tool && inside(p, selection)) {
        const image = toImage(p);
        if (tool === "text") {
          setTyping({ at: image, css: p, value: "" });
          return;
        }
        if (tool === "number") {
          setHistory((h) => commit(h, [...h.present, { kind: "number", color, width, at: image, n: nextNumber(h.present) }]));
          return;
        }
        setDraft(tool === "pen" || tool === "highlighter"
          ? { kind: tool, color, width, points: [image] }
          : { kind: tool, color, width, from: image, to: image });
        setDrag({ kind: "draw" });
        return;
      }
      if (inside(p, selection)) { setDrag({ kind: "move", from: p, start: selection }); return; }
      if (tool) return;
    }
    bridge?.selecting();
    setReleased(false);
    setOcr(null);
    setDrag({ kind: "create", from: p });
  }

  function onPointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const p = at(event);
    setPointer(p);
    if (!drag) return;
    if (drag.kind === "create") {
      if (Math.hypot(p.x - drag.from.x, p.y - drag.from.y) < DRAG_THRESHOLD && !selection) return;
      let to = p;
      if (event.shiftKey) {
        const side = Math.max(Math.abs(p.x - drag.from.x), Math.abs(p.y - drag.from.y));
        to = { x: drag.from.x + Math.sign(p.x - drag.from.x || 1) * side, y: drag.from.y + Math.sign(p.y - drag.from.y || 1) * side };
      }
      setSelection(clampRect(normalized(drag.from, to), screenRect));
    } else if (drag.kind === "move") {
      setSelection(moveRect(drag.start, p.x - drag.from.x, p.y - drag.from.y, screenRect));
      setOcr(null);
    } else if (drag.kind === "resize") {
      setSelection(resizeRect(drag.start, drag.handle, p.x - drag.from.x, p.y - drag.from.y, screenRect));
      setOcr(null);
    } else if (drag.kind === "draw" && draft) {
      const image = toImage(p);
      if ("points" in draft) {
        setDraft({ ...draft, points: [...draft.points, image] });
      } else if ("from" in draft) {
        let to = image;
        if (event.shiftKey && (draft.kind === "rect" || draft.kind === "ellipse")) {
          const side = Math.max(Math.abs(image.x - draft.from.x), Math.abs(image.y - draft.from.y));
          to = { x: draft.from.x + Math.sign(image.x - draft.from.x || 1) * side, y: draft.from.y + Math.sign(image.y - draft.from.y || 1) * side };
        }
        setDraft({ ...draft, to });
      }
    }
  }

  function onPointerUp(event: React.PointerEvent<HTMLDivElement>) {
    const p = at(event);
    if (drag?.kind === "create") {
      const moved = Math.hypot(p.x - drag.from.x, p.y - drag.from.y) >= DRAG_THRESHOLD;
      if (!moved) {
        // A click takes the window under the pointer, or the whole display.
        const hit = windows.find(({ rect }) => inside(p, rect));
        setSelection(hit ? clampRect(hit.rect, screenRect) : screenRect);
      } else if (selection && (selection.width < 4 || selection.height < 4)) {
        setSelection(null);
      }
    } else if (drag?.kind === "draw" && draft) {
      if (!isEmpty(draft)) setHistory((h) => commit(h, [...h.present, draft]));
      setDraft(null);
    }
    setDrag(null);
  }

  function onContextMenu(event: React.MouseEvent) {
    event.preventDefault();
    // Right click: back to choosing a region, or out when nothing is chosen.
    if (selection) {
      setSelection(null);
      setTool(null);
      setOcr(null);
      setTyping(null);
      setHistory(emptyHistory);
    } else {
      void finish("cancel");
    }
  }

  function onDoubleClick(event: React.MouseEvent) {
    if (selection && !tool && inside(at(event), selection)) void finish("copy");
  }

  // -- keys
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (!frame) return;
      const target = event.target as HTMLElement | null;
      const editing = target?.tagName === "INPUT" || target?.tagName === "TEXTAREA";
      const mod = event.metaKey || event.ctrlKey;
      if (event.key === "Escape") {
        event.preventDefault();
        if (typing) { commitText(); return; }
        void finish("cancel");
        return;
      }
      if (editing) return;
      if (mod && event.key.toLowerCase() === "z") {
        event.preventDefault();
        setHistory((h) => (event.shiftKey ? redo(h) : undo(h)));
        return;
      }
      if (mod && event.key.toLowerCase() === "y") { event.preventDefault(); setHistory(redo); return; }
      if (mod && event.key.toLowerCase() === "a") { event.preventDefault(); bridge?.selecting(); setSelection(screenRect); return; }
      if (selection && (event.key === "Enter" || (mod && event.key.toLowerCase() === "c"))) {
        event.preventDefault();
        void finish("copy");
        return;
      }
      if (mod && event.key.toLowerCase() === "s" && selection) { event.preventDefault(); void finish("save"); return; }
      if (!selection && !mod && event.key.toLowerCase() === "c" && pointer && baseRef.current) {
        const ctx = baseRef.current.getContext("2d");
        const [r, g, b] = ctx?.getImageData(Math.floor(pointer.x * scale), Math.floor(pointer.y * scale), 1, 1).data ?? [0, 0, 0];
        void desktopBridge()?.writeClipboardText?.(toHex(r, g, b));
        void finish("cancel");
        return;
      }
      if (selection && !mod && event.key.startsWith("Arrow")) {
        event.preventDefault();
        const step = event.shiftKey ? 10 : 1;
        const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
        const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
        setSelection((s) => (s ? moveRect(s, dx, dy, screenRect) : s));
        return;
      }
      const picked = TOOL_KEYS[event.key.toLowerCase()];
      if (selection && !mod && picked) setTool((current) => (current === picked ? null : picked));
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [frame, typing, commitText, finish, selection, pointer, scale, screenRect, bridge]);

  // -- magnifier
  const showMagnifier = Boolean(pointer && frame && (!selection || drag?.kind === "create" || drag?.kind === "resize"));
  const [hex, setHex] = useState("#000000");
  useEffect(() => {
    const canvas = magnifierRef.current;
    const base = baseRef.current;
    if (!showMagnifier || !canvas || !base || !pointer) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = MAGNIFIER_SIZE * ratio;
    canvas.height = MAGNIFIER_SIZE * ratio;
    const px = Math.floor(pointer.x * scale);
    const py = Math.floor(pointer.y * scale);
    const half = Math.floor(MAGNIFIER_PIXELS / 2);
    ctx.imageSmoothingEnabled = false;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(base, px - half, py - half, MAGNIFIER_PIXELS, MAGNIFIER_PIXELS, 0, 0, canvas.width, canvas.height);
    const cell = canvas.width / MAGNIFIER_PIXELS;
    ctx.strokeStyle = "rgba(59,130,246,0.9)";
    ctx.lineWidth = Math.max(1, ratio);
    ctx.strokeRect(half * cell, half * cell, cell, cell);
    const [r, g, b] = base.getContext("2d")?.getImageData(px, py, 1, 1).data ?? [0, 0, 0];
    setHex(toHex(r, g, b));
  }, [showMagnifier, pointer, scale]);

  if (!bridge) return null;

  const outline = selection ?? hovered;
  const editing = Boolean(selection && !(drag?.kind === "create"));
  const magnifierAt = pointer ? {
    left: pointer.x + 20 + MAGNIFIER_SIZE + 8 > view.width ? pointer.x - 20 - MAGNIFIER_SIZE : pointer.x + 20,
    top: pointer.y + 20 + MAGNIFIER_SIZE + 48 > view.height ? pointer.y - 20 - MAGNIFIER_SIZE - 44 : pointer.y + 20,
  } : null;
  const cursor = drag?.kind === "move" ? "move"
    : selection && pointer && handleAt(pointer, selection, 6) ? `${handleAt(pointer, selection, 6)}-resize`
      : selection && pointer && inside(pointer, selection) ? (tool === "text" ? "text" : tool ? "crosshair" : "move")
        : "crosshair";
  const ocrPlace = ocr && selection ? placeBeside(selection, ocrSize, screenRect) : null;

  return (
    <div className={styles.root} style={{ cursor }} onPointerDown={onPointerDown} onPointerMove={onPointerMove}
      onPointerUp={onPointerUp} onPointerCancel={onPointerUp} onContextMenu={onContextMenu} onDoubleClick={onDoubleClick}>
      <canvas ref={baseRef} className={styles.layer} />
      <canvas ref={marksRef} className={styles.layer} />
      {outline ? (
        <div className={styles.selection} data-hover={!selection || undefined}
          style={{ left: outline.x, top: outline.y, width: outline.width, height: outline.height }}>
          {selection || hovered ? (
            <span className={styles.size} data-below={outline.y < 28 || undefined}>
              {Math.round(outline.width * scale)} × {Math.round(outline.height * scale)}
              {!selection && hovered && hovered !== screenRect
                ? `　${windows.find((w) => clampRect(w.rect, screenRect).x === hovered.x && clampRect(w.rect, screenRect).y === hovered.y)?.app || ""}`
                : ""}
            </span>
          ) : null}
          {editing ? HANDLES.map((h) => <span key={h} className={styles.handle} data-handle={h} />) : null}
        </div>
      ) : (
        <div className={styles.dimAll} />
      )}
      {ocr && selection ? ocr.lines.map((line, i) => (
        <span key={i} className={styles.ocrBox} style={{
          left: selection.x + line.box[0] / scale, top: selection.y + line.box[1] / scale,
          width: line.box[2] / scale, height: line.box[3] / scale,
        }} />
      )) : null}
      {typing ? (
        <input autoFocus data-screenshot-ui className={styles.textInput}
          style={{ left: typing.css.x, top: typing.css.y, color, fontSize: `${width <= 2 ? 14 : width <= 4 ? 18 : 26}px` }}
          value={typing.value} aria-label={text("Annotation text", "标注文字")}
          onChange={(event) => setTyping({ ...typing, value: event.target.value })}
          onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); commitText(); } }}
          onBlur={commitText} />
      ) : null}
      {selection && toolbarPlace && drag?.kind !== "create" ? (
        <div ref={setToolbarNode} data-screenshot-ui className={styles.toolbar}
          style={{ left: toolbarPlace.x, top: toolbarPlace.y }}
          onPointerDown={(event) => event.stopPropagation()}>
          <AnnotateToolbar
            tool={tool} onToolChange={(next) => { commitText(); setTool(next); }}
            color={color} onColorChange={setColor} width={width} onWidthChange={setWidth}
            canUndo={history.past.length > 0} canRedo={history.future.length > 0}
            onUndo={() => setHistory(undo)} onRedo={() => setHistory(redo)}
            styleAbove={toolbarPlace.side === "above"}
            actions={(
              <>
                <ActionButton label={text("Recognize text", "识字")} active={Boolean(ocr)} icon="text-selection"
                  onClick={() => { void recognize(); }} />
                <ActionButton label={text("Pin to screen", "钉在屏幕上")} icon="pin" onClick={() => { void finish("pin"); }} />
                <ActionButton label={text("Save", "保存")} detail={mac ? "⌘S" : "Ctrl+S"} icon="download-minimalistic"
                  onClick={() => { void finish("save"); }} />
                <ActionButton label={text("Copy", "复制")} detail={text("Enter or double-click", "回车或双击")} icon="copy"
                  onClick={() => { void finish("copy"); }} />
                <ActionButton label={text("Cancel", "取消")} detail="Esc" icon="close-circle"
                  onClick={() => { void finish("cancel"); }} />
                <ActionButton primary label={text("Send to chat", "发送到对话")}
                  detail={text("Adds it to the message box", "加到输入框里")} icon="plain-2"
                  onClick={() => { void finish("send"); }} />
              </>
            )}
          />
        </div>
      ) : null}
      {ocr && ocrPlace ? (
        <div ref={ocrRef} data-screenshot-ui className={styles.ocr} style={{ left: ocrPlace.x, top: ocrPlace.y }}
          onPointerDown={(event) => event.stopPropagation()}>
          {ocr.lines.length ? (
            <textarea className={styles.ocrText} defaultValue={ocrText} spellCheck={false}
              aria-label={text("Recognized text", "识别出的文字")}
              rows={Math.min(14, Math.max(3, ocr.lines.length))} />
          ) : (
            <p className={styles.ocrEmpty}>{text("No text in the selection.", "选区里没有文字。")}</p>
          )}
          {ocr.lines.length ? (
            <div className={styles.ocrActions}>
              <Button size="xs" onClick={() => {
                const area = ocrRef.current?.querySelector("textarea");
                const chosen = area && area.selectionEnd > area.selectionStart
                  ? area.value.slice(area.selectionStart, area.selectionEnd) : area?.value ?? ocrText;
                void desktopBridge()?.writeClipboardText?.(chosen);
              }}>
                {text("Copy", "复制")}
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}
      {showMagnifier && magnifierAt && pointer ? (
        <div className={styles.magnifier} style={magnifierAt}>
          <canvas ref={magnifierRef} style={{ width: MAGNIFIER_SIZE, height: MAGNIFIER_SIZE }} />
          <div className={styles.magnifierRow}>
            <span>{Math.round(pointer.x * scale)}, {Math.round(pointer.y * scale)}</span>
          </div>
          <div className={styles.magnifierRow}>
            <span><i className={styles.chip} style={{ background: hex }} />{hex}</span>
            <span className={styles.muted}>C</span>
          </div>
        </div>
      ) : null}
      {!selection && !drag ? (
        <div className={styles.hint}>
          {text("Click a window or drag a region · Esc to cancel", "点击选中窗口，拖动框选区域 · Esc 取消")}
        </div>
      ) : null}
    </div>
  );
}
