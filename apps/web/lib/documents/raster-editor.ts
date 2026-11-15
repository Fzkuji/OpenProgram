"use client";
import type { DocumentController } from "@/lib/files/document/document-controller";
import { assertEncodedRaster, validateRasterDecoded } from "./raster-format";

type Crop = { left: number; top: number; width: number; height: number };
type Point = { x: number; y: number };
type Snapshot = { blob: Blob; width: number; height: number };
type FabricRuntime = Pick<typeof import("fabric"), "Canvas" | "IText" | "Rect">;
type FabricCanvasInstance = InstanceType<FabricRuntime["Canvas"]>;

const clamp = (value: number, minimum: number, maximum: number) => Math.min(maximum, Math.max(minimum, value));

function canvasBlob(canvas: HTMLCanvasElement, type: string, quality?: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((value) => value ? resolve(value) : reject(new Error("Image encoding failed.")), type, quality);
  });
}

function canvasSnapshot(canvas: HTMLCanvasElement): Promise<Snapshot> {
  const copy = document.createElement("canvas");
  copy.width = canvas.width;
  copy.height = canvas.height;
  const context = copy.getContext("2d");
  if (!context) return Promise.reject(new Error("Image editing is unavailable."));
  context.drawImage(canvas, 0, 0);
  return canvasBlob(copy, "image/png").then((blob) => ({ blob, width: copy.width, height: copy.height }));
}

class NativeRasterEditor {
  private readonly surface: HTMLDivElement;
  private readonly frame: HTMLDivElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly annotationElement: HTMLCanvasElement;
  private readonly overlay: HTMLCanvasElement;
  private readonly annotationCanvas: FabricCanvasInstance;
  private readonly fabric: FabricRuntime;
  private readonly context: CanvasRenderingContext2D;
  private readonly overlayContext: CanvasRenderingContext2D;
  private readonly undoStack: Snapshot[] = [];
  private readonly redoStack: Snapshot[] = [];
  private readonly resizeObserver: ResizeObserver | null;
  private readonly onUserChange: () => void;
  private mode: "idle" | "crop" | "draw" = "idle";
  private cropRect: Crop | null = null;
  private pointerStart: Point | null = null;
  private lastPoint: Point | null = null;
  private drawSnapshot: Promise<Snapshot> | null = null;
  private pendingDrawing: Promise<void> | null = null;
  private annotationSnapshot: Promise<Snapshot> | null = null;
  private drew = false;
  private destroyed = false;
  private restoring = false;
  private interactive = true;

  constructor(container: HTMLElement, bitmap: ImageBitmap, onUserChange: () => void, fabric: FabricRuntime) {
    this.onUserChange = onUserChange;
    this.fabric = fabric;
    this.surface = document.createElement("div");
    this.surface.style.cssText = "width:100%;height:100%;display:flex;align-items:center;justify-content:center;overflow:hidden;background:transparent;";
    this.frame = document.createElement("div");
    this.frame.style.cssText = "position:relative;flex:none;line-height:0;";
    this.canvas = document.createElement("canvas");
    this.canvas.setAttribute("role", "img");
    this.canvas.setAttribute("aria-label", "Image editor canvas");
    this.canvas.style.cssText = "display:block;width:100%;height:100%;object-fit:contain;";
    this.annotationElement = document.createElement("canvas");
    this.annotationElement.setAttribute("aria-hidden", "true");
    this.overlay = document.createElement("canvas");
    this.overlay.setAttribute("aria-hidden", "true");
    this.overlay.style.cssText = "position:absolute;inset:0;width:100%;height:100%;touch-action:none;pointer-events:none;";
    this.frame.append(this.canvas, this.annotationElement, this.overlay);
    this.surface.append(this.frame);
    container.replaceChildren(this.surface);

    this.canvas.width = bitmap.width;
    this.canvas.height = bitmap.height;
    this.annotationElement.width = bitmap.width;
    this.annotationElement.height = bitmap.height;
    this.overlay.width = bitmap.width;
    this.overlay.height = bitmap.height;
    const context = this.canvas.getContext("2d");
    const overlayContext = this.overlay.getContext("2d");
    if (!context || !overlayContext) throw new Error("Image editing is unavailable.");
    this.context = context;
    this.overlayContext = overlayContext;
    this.context.imageSmoothingEnabled = true;
    this.context.drawImage(bitmap, 0, 0);

    this.annotationCanvas = new fabric.Canvas(this.annotationElement, {
      width: bitmap.width,
      height: bitmap.height,
      selection: true,
      preserveObjectStacking: true,
      enableRetinaScaling: false,
    });
    this.annotationCanvas.wrapperEl.style.cssText = "position:absolute;inset:0;width:100%;height:100%;";
    this.annotationCanvas.on("mouse:down", this.handleAnnotationMouseDown);
    this.annotationCanvas.on("object:modified", this.handleAnnotationModified);
    this.annotationCanvas.on("text:editing:entered", this.handleAnnotationEditingEntered);
    this.annotationCanvas.on("text:editing:exited", this.handleAnnotationEditingExited);
    this.setAnnotationInput();

    this.resizeObserver = typeof ResizeObserver === "function" ? new ResizeObserver(() => this.layout()) : null;
    this.resizeObserver?.observe(this.surface);
    this.layout();
    this.overlay.addEventListener("pointerdown", this.handlePointerDown);
    this.overlay.addEventListener("pointermove", this.handlePointerMove);
    this.overlay.addEventListener("pointerup", this.handlePointerUp);
    this.overlay.addEventListener("pointercancel", this.handlePointerCancel);
  }

  private readonly handlePointerDown = (event: PointerEvent): void => {
    if (this.destroyed || this.restoring || !this.interactive || this.mode === "idle") return;
    event.preventDefault();
    this.overlay.setPointerCapture(event.pointerId);
    const point = this.pointFromEvent(event);
    this.pointerStart = point;
    this.lastPoint = point;
    if (this.mode === "crop") {
      this.cropRect = { left: point.x, top: point.y, width: 1, height: 1 };
      this.drawCropOverlay();
    } else {
      this.drawSnapshot = canvasSnapshot(this.renderedCanvas());
      this.drew = false;
      this.drawLine(point, point);
      this.drew = true;
    }
  };

  private readonly handlePointerMove = (event: PointerEvent): void => {
    if (!this.pointerStart || this.destroyed || this.restoring || !this.interactive) return;
    event.preventDefault();
    const point = this.pointFromEvent(event);
    if (this.mode === "crop") {
      this.cropRect = this.rectFromPoints(this.pointerStart, point);
      this.drawCropOverlay();
    } else if (this.lastPoint) {
      this.drawLine(this.lastPoint, point);
      this.lastPoint = point;
      this.drew = true;
    }
  };

  private readonly handlePointerUp = (event: PointerEvent): void => {
    if (!this.pointerStart) return;
    event.preventDefault();
    if (this.overlay.hasPointerCapture(event.pointerId)) this.overlay.releasePointerCapture(event.pointerId);
    if (this.restoring) {
      this.pointerStart = null;
      this.lastPoint = null;
      this.drawSnapshot = null;
      this.drew = false;
      return;
    }
    if (this.mode === "draw" && this.drew && this.drawSnapshot) {
      const snapshot = this.drawSnapshot;
      const pending = (this.pendingDrawing ?? Promise.resolve())
        .then(() => snapshot)
        .then((before) => {
          if (this.destroyed) return;
          this.pushUndo(before);
          this.clearRedo();
          this.onUserChange();
        })
        .finally(() => {
          if (this.pendingDrawing === pending) this.pendingDrawing = null;
        });
      this.pendingDrawing = pending;
      void pending;
    }
    this.pointerStart = null;
    this.lastPoint = null;
    this.drawSnapshot = null;
    this.drew = false;
  };

  private readonly handlePointerCancel = (event: PointerEvent): void => {
    if (this.overlay.hasPointerCapture(event.pointerId)) this.overlay.releasePointerCapture(event.pointerId);
    if (!this.restoring && this.mode === "draw" && this.drew && this.drawSnapshot) {
      const snapshot = this.drawSnapshot;
      const pending = (this.pendingDrawing ?? Promise.resolve())
        .then(() => snapshot)
        .then((before) => this.restore(before))
        .finally(() => {
          if (this.pendingDrawing === pending) this.pendingDrawing = null;
        });
      this.pendingDrawing = pending;
      void pending;
    }
    if (this.mode === "crop") {
      this.cropRect = null;
      this.drawCropOverlay();
    }
    this.pointerStart = null;
    this.lastPoint = null;
    this.drawSnapshot = null;
    this.drew = false;
  };

  private readonly handleAnnotationMouseDown = (): void => {
    if (this.destroyed || this.restoring || !this.interactive || this.mode !== "idle") return;
    if (this.annotationCanvas.getActiveObject()) this.annotationSnapshot = this.snapshot();
  };

  private readonly handleAnnotationModified = (): void => {
    if (this.destroyed || this.restoring || !this.annotationSnapshot) return;
    const snapshot = this.annotationSnapshot;
    this.annotationSnapshot = null;
    void snapshot.then((before) => {
      if (this.destroyed || this.restoring) return;
      this.pushUndo(before);
      this.clearRedo();
      this.onUserChange();
    });
  };

  private readonly handleAnnotationEditingEntered = (): void => {
    if (this.destroyed || this.restoring || !this.interactive || this.mode !== "idle") return;
    this.annotationSnapshot = this.snapshot();
  };

  private readonly handleAnnotationEditingExited = (): void => {
    this.handleAnnotationModified();
  };

  private setAnnotationInput(): void {
    const enabled = this.interactive && !this.destroyed && !this.restoring && this.mode === "idle";
    this.annotationCanvas.selection = enabled;
    for (const object of this.annotationCanvas.getObjects()) {
      object.selectable = enabled;
      object.evented = enabled;
    }
    this.annotationCanvas.upperCanvasEl.style.pointerEvents = enabled ? "auto" : "none";
    this.annotationCanvas.requestRenderAll();
  }

  private renderedCanvas(): HTMLCanvasElement {
    const copy = document.createElement("canvas");
    copy.width = this.canvas.width;
    copy.height = this.canvas.height;
    const context = copy.getContext("2d");
    if (!context) throw new Error("Image editing is unavailable.");
    context.drawImage(this.canvas, 0, 0);
    context.drawImage(this.annotationCanvas.toCanvasElement(), 0, 0);
    return copy;
  }

  private clearAnnotations(): void {
    const wasRestoring = this.restoring;
    this.restoring = true;
    try {
      this.annotationCanvas.discardActiveObject();
      this.annotationCanvas.clear();
      this.annotationCanvas.setDimensions({ width: this.canvas.width, height: this.canvas.height });
      this.annotationCanvas.requestRenderAll();
    } finally {
      this.restoring = wasRestoring;
      this.setAnnotationInput();
    }
  }

  private layout(): void {
    if (this.destroyed || !this.canvas.width || !this.canvas.height) return;
    const width = this.surface.clientWidth;
    const height = this.surface.clientHeight;
    if (!width || !height) return;
    const scale = Math.min(width / this.canvas.width, height / this.canvas.height, 1);
    this.frame.style.width = `${Math.max(1, Math.floor(this.canvas.width * scale))}px`;
    this.frame.style.height = `${Math.max(1, Math.floor(this.canvas.height * scale))}px`;
    this.annotationCanvas.wrapperEl.style.width = "100%";
    this.annotationCanvas.wrapperEl.style.height = "100%";
    this.annotationCanvas.getElement().style.width = "100%";
    this.annotationCanvas.getElement().style.height = "100%";
    this.annotationCanvas.upperCanvasEl.style.width = "100%";
    this.annotationCanvas.upperCanvasEl.style.height = "100%";
  }

  private pointFromEvent(event: PointerEvent): Point {
    const bounds = this.canvas.getBoundingClientRect();
    return {
      x: clamp((event.clientX - bounds.left) * this.canvas.width / Math.max(1, bounds.width), 0, this.canvas.width),
      y: clamp((event.clientY - bounds.top) * this.canvas.height / Math.max(1, bounds.height), 0, this.canvas.height),
    };
  }

  private rectFromPoints(start: Point, end: Point): Crop {
    const left = clamp(Math.min(start.x, end.x), 0, this.canvas.width - 1);
    const top = clamp(Math.min(start.y, end.y), 0, this.canvas.height - 1);
    const right = clamp(Math.max(start.x, end.x), left + 1, this.canvas.width);
    const bottom = clamp(Math.max(start.y, end.y), top + 1, this.canvas.height);
    return { left, top, width: right - left, height: bottom - top };
  }

  private drawCropOverlay(): void {
    this.overlayContext.clearRect(0, 0, this.overlay.width, this.overlay.height);
    if (!this.cropRect) return;
    const { left, top, width, height } = this.cropRect;
    this.overlayContext.save();
    this.overlayContext.fillStyle = "rgb(0 0 0 / 0.24)";
    this.overlayContext.fillRect(0, 0, this.overlay.width, this.overlay.height);
    this.overlayContext.clearRect(left, top, width, height);
    this.overlayContext.strokeStyle = "#ffffff";
    this.overlayContext.lineWidth = Math.max(1, this.canvas.width / 600);
    this.overlayContext.setLineDash([8, 5]);
    this.overlayContext.strokeRect(left, top, width, height);
    this.overlayContext.restore();
  }

  private drawLine(start: Point, end: Point): void {
    this.context.save();
    this.context.strokeStyle = "#ff00ff";
    this.context.lineWidth = Math.max(2, this.canvas.width / 400);
    this.context.lineCap = "round";
    this.context.lineJoin = "round";
    this.context.beginPath();
    this.context.moveTo(start.x, start.y);
    this.context.lineTo(end.x, end.y);
    this.context.stroke();
    this.context.restore();
  }

  private async restore(snapshot: Snapshot): Promise<void> {
    this.restoring = true;
    this.overlay.style.pointerEvents = "none";
    this.setAnnotationInput();
    let image: ImageBitmap | null = null;
    try {
      image = await createImageBitmap(snapshot.blob);
      this.clearAnnotations();
      this.canvas.width = snapshot.width;
      this.canvas.height = snapshot.height;
      this.annotationElement.width = snapshot.width;
      this.annotationElement.height = snapshot.height;
      this.overlay.width = snapshot.width;
      this.overlay.height = snapshot.height;
      this.annotationCanvas.setDimensions({ width: snapshot.width, height: snapshot.height });
      this.context.clearRect(0, 0, snapshot.width, snapshot.height);
      this.context.drawImage(image, 0, 0);
      this.cropRect = null;
      this.drawCropOverlay();
      this.layout();
    } finally {
      image?.close();
      this.restoring = false;
      this.overlay.style.pointerEvents = this.interactive && !this.destroyed && this.mode !== "idle" ? "auto" : "none";
      this.setAnnotationInput();
    }
  }

  async snapshot(): Promise<Snapshot> {
    await this.waitForPendingDrawing();
    return canvasSnapshot(this.renderedCanvas());
  }

  async waitForPendingDrawing(): Promise<void> { await this.pendingDrawing; }

  pushUndo(snapshot: Snapshot): void {
    this.undoStack.push(snapshot);
    while (this.undoStack.length > 50) this.undoStack.shift();
  }
  clearRedo(): void { this.redoStack.length = 0; }

  setInteractive(value: boolean): void {
    this.interactive = value;
    this.overlay.style.pointerEvents = value && !this.restoring && this.mode !== "idle" ? "auto" : "none";
    this.setAnnotationInput();
  }

  setMode(mode: "idle" | "crop" | "draw"): void {
    this.mode = mode;
    this.overlay.style.pointerEvents = this.interactive && !this.restoring && mode !== "idle" ? "auto" : "none";
    this.setAnnotationInput();
  }

  startCrop(): void {
    this.setMode("crop");
    this.cropRect = null;
    this.drawCropOverlay();
  }

  getCropRect(): Crop | null {
    return this.cropRect;
  }

  async crop(value: Crop): Promise<void> {
    const copy = document.createElement("canvas");
    copy.width = Math.round(value.width);
    copy.height = Math.round(value.height);
    const context = copy.getContext("2d");
    if (!context) throw new Error("Image editing is unavailable.");
    context.drawImage(this.renderedCanvas(), -Math.round(value.left), -Math.round(value.top));
    this.clearAnnotations();
    this.canvas.width = copy.width;
    this.canvas.height = copy.height;
    this.annotationElement.width = copy.width;
    this.annotationElement.height = copy.height;
    this.overlay.width = copy.width;
    this.overlay.height = copy.height;
    this.annotationCanvas.setDimensions({ width: copy.width, height: copy.height });
    this.context.drawImage(copy, 0, 0);
    this.cropRect = null;
    this.setMode("idle");
    this.drawCropOverlay();
    this.layout();
  }

  async rotate(): Promise<void> {
    const copy = document.createElement("canvas");
    copy.width = this.canvas.height;
    copy.height = this.canvas.width;
    const context = copy.getContext("2d");
    if (!context) throw new Error("Image editing is unavailable.");
    context.translate(copy.width, 0);
    context.rotate(Math.PI / 2);
    context.drawImage(this.renderedCanvas(), 0, 0);
    this.clearAnnotations();
    this.canvas.width = copy.width;
    this.canvas.height = copy.height;
    this.annotationElement.width = copy.width;
    this.annotationElement.height = copy.height;
    this.overlay.width = copy.width;
    this.overlay.height = copy.height;
    this.annotationCanvas.setDimensions({ width: copy.width, height: copy.height });
    this.context.drawImage(copy, 0, 0);
    this.cropRect = null;
    this.drawCropOverlay();
    this.layout();
  }

  async addText(text: string): Promise<void> {
    const annotation = new this.fabric.IText(text, {
      left: this.canvas.width / 2,
      top: this.canvas.height / 2,
      originX: "center",
      originY: "center",
      fontSize: Math.max(16, Math.round(this.canvas.width / 40)),
      fill: "#111111",
    });
    this.annotationCanvas.add(annotation);
    this.annotationCanvas.setActiveObject(annotation);
    this.annotationCanvas.requestRenderAll();
  }

  async addShape(type: string): Promise<void> {
    if (type !== "rect") throw new Error(`Unsupported shape: ${type}`);
    const width = Math.max(20, Math.round(this.canvas.width / 8));
    const height = Math.max(20, Math.round(this.canvas.height / 8));
    const annotation = new this.fabric.Rect({
      left: this.canvas.width / 2,
      top: this.canvas.height / 2,
      originX: "center",
      originY: "center",
      width,
      height,
      fill: "#ff00ff",
      stroke: "#ff00ff",
      strokeWidth: Math.max(2, this.canvas.width / 400),
    });
    this.annotationCanvas.add(annotation);
    this.annotationCanvas.setActiveObject(annotation);
    this.annotationCanvas.requestRenderAll();
  }

  cancelTool(): void {
    this.cropRect = null;
    this.setMode("idle");
    this.drawCropOverlay();
  }

  async undo(): Promise<boolean> {
    await this.waitForPendingDrawing();
    const snapshot = this.undoStack.pop();
    if (!snapshot) return false;
    this.redoStack.push(await this.snapshot());
    await this.restore(snapshot);
    return true;
  }

  async redo(): Promise<boolean> {
    await this.waitForPendingDrawing();
    const snapshot = this.redoStack.pop();
    if (!snapshot) return false;
    this.undoStack.push(await this.snapshot());
    await this.restore(snapshot);
    return true;
  }

  toDataURL(format: string, quality: number): string {
    return this.renderedCanvas().toDataURL(`image/${format === "jpeg" ? "jpeg" : format}`, quality);
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.resizeObserver?.disconnect();
    this.overlay.removeEventListener("pointerdown", this.handlePointerDown);
    this.overlay.removeEventListener("pointermove", this.handlePointerMove);
    this.overlay.removeEventListener("pointerup", this.handlePointerUp);
    this.overlay.removeEventListener("pointercancel", this.handlePointerCancel);
    this.annotationCanvas.off("mouse:down", this.handleAnnotationMouseDown);
    this.annotationCanvas.off("object:modified", this.handleAnnotationModified);
    this.annotationCanvas.off("text:editing:entered", this.handleAnnotationEditingEntered);
    this.annotationCanvas.off("text:editing:exited", this.handleAnnotationEditingExited);
    void this.annotationCanvas.dispose();
    this.surface.remove();
    this.undoStack.length = 0;
    this.redoStack.length = 0;
  }
}

export interface RasterEditorInstance {
  save(): Promise<File>; flushPendingSaves(): Promise<void>;
  setReadonly(readonly: boolean): void; setInputEnabled(enabled: boolean): void;
  destroy(): Promise<void>; getState(): { dirty: boolean; readonly: boolean; destroyed: boolean; status: string };
  rotate(): Promise<void>; crop(options: Crop): Promise<void>;
  addText(text: string): Promise<void>; addShape(type: string): Promise<void>;
  draw(): Promise<void>; startCrop(): Promise<void>; applyCrop(): Promise<void>; cancelTool(): Promise<void>; undo(): Promise<void>; redo(): Promise<void>;
}

const nextFrame = () => new Promise<void>((resolve) => {
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => resolve());
  else setTimeout(resolve, 0);
});

export async function createBoundRasterEditor(options: {
  container: HTMLElement; controller: DocumentController; bytes: Blob; fileName: string; readonly?: boolean;
  generation?: number; signal?: AbortSignal;
}): Promise<RasterEditorInstance> {
  const generation = options.generation ?? options.controller.getState().editorRevision;
  options.signal?.throwIfAborted();
  const checked = await validateRasterDecoded(options.bytes);
  const extension = options.fileName.toLowerCase().split(".").pop();
  if ((extension === "jpg" ? "jpeg" : extension) !== checked.format)
    throw new Error("UNSUPPORTED_IMAGE: file extension and raster encoding differ.");
  const fabric = await import("fabric");
  options.signal?.throwIfAborted();
  const bitmap = await createImageBitmap(options.bytes);
  try {
    options.signal?.throwIfAborted();
  } catch (error) {
    bitmap.close();
    throw error;
  }

  let readonly = Boolean(options.readonly), enabled = true, destroyed = false, dirty = false, revision = 0;
  let commands: Promise<unknown> = Promise.resolve(), saves: Promise<unknown> = Promise.resolve();
  let detach: (() => void) | undefined;
  const markDirty = () => {
    if (destroyed) return;
    revision++;
    dirty = true;
    options.controller.markRichEditorDirty(true, instance);
  };
  let editor: NativeRasterEditor;
  try {
    editor = new NativeRasterEditor(options.container, bitmap, markDirty, fabric);
  } catch (error) {
    bitmap.close();
    throw error;
  }
  bitmap.close();
  const syncInput = () => editor.setInteractive(!readonly && enabled && !destroyed);
  const run = (operation: () => Promise<unknown>, stopDrawing = true): Promise<void> => {
    if (readonly || !enabled || destroyed) return Promise.reject(new Error("Raster editor is read-only."));
    const task = commands.then(async () => {
      if (destroyed) throw new Error("The image editor was closed.");
      if (stopDrawing) editor.cancelTool();
      const snapshot = await editor.snapshot();
      await operation();
      editor.pushUndo(snapshot);
      editor.clearRedo();
      markDirty();
    });
    commands = task.catch(() => undefined);
    return task;
  };
  const historyRun = (operation: () => Promise<unknown>): Promise<void> => {
    if (readonly || !enabled || destroyed) return Promise.reject(new Error("Raster editor is read-only."));
    const task = commands.then(async () => {
      if (destroyed) throw new Error("The image editor was closed.");
      editor.cancelTool();
      await operation();
      markDirty();
    });
    commands = task.catch(() => undefined);
    return task;
  };
  const toolRun = (operation: () => void): Promise<void> => {
    if (readonly || !enabled || destroyed) return Promise.reject(new Error("Raster editor is read-only."));
    const task = commands.then(() => {
      if (destroyed) throw new Error("The image editor was closed.");
      operation();
    });
    commands = task.catch(() => undefined);
    return task;
  };
  const exportFile = async () => {
    await editor.waitForPendingDrawing();
    await commands;
    await nextFrame();
    await nextFrame();
    if (destroyed) throw new Error("The image editor was closed.");
    const captured = revision;
    const url = editor.toDataURL(checked.format, 0.95);
    const blob = await (await fetch(url)).blob();
    await assertEncodedRaster(blob, checked.format);
    const file = new File([blob], options.fileName, { type: checked.mime });
    if (destroyed) throw new Error("The image editor was closed.");
    if (!options.readonly) await options.controller.stageRichExport(file, generation);
    dirty = revision !== captured;
    return file;
  };
  const instance: RasterEditorInstance = {
    save() { const task = saves.then(exportFile); saves = task.catch(() => undefined); return task; },
    async flushPendingSaves() { let last: Promise<unknown>; do { last = saves; await last; await editor.waitForPendingDrawing(); await commands; } while (last !== saves); },
    setReadonly(value) { readonly = value; syncInput(); },
    setInputEnabled(value) { enabled = value; syncInput(); },
    async destroy() {
      if (destroyed) return;
      destroyed = true;
      syncInput();
      await editor.waitForPendingDrawing();
      await commands;
      await saves;
      editor.destroy();
      detach?.();
      detach = undefined;
    },
    getState() { return { dirty, readonly, destroyed, status: destroyed ? "destroyed" : "ready" }; },
    rotate: () => run(() => editor.rotate()),
    crop: (value) => run(async () => {
      const current = editor.getCropRect();
      if (!current) throw new Error("Select a crop area before applying crop.");
      if (![value.left, value.top, value.width, value.height].every(Number.isFinite) || value.left < 0 || value.top < 0 || value.width < 1 || value.height < 1 || value.left + value.width > current.left + current.width || value.top + value.height > current.top + current.height)
        throw new Error("The crop must be inside the current image.");
      await editor.crop(value);
    }, false),
    addText: (text) => run(() => editor.addText(text)),
    addShape: (type) => run(() => editor.addShape(type)),
    draw: () => toolRun(() => {
      editor.cancelTool();
      editor.setMode("draw");
      syncInput();
    }),
    startCrop: () => toolRun(() => editor.startCrop()),
    applyCrop: () => {
      const value = editor.getCropRect();
      if (!value) return Promise.reject(new Error("Select a crop area before applying crop."));
      return instance.crop({ ...value });
    },
    cancelTool: () => toolRun(() => editor.cancelTool()),
    undo: () => historyRun(async () => { if (!await editor.undo()) throw new Error("Nothing to undo."); }),
    redo: () => historyRun(async () => { if (!await editor.redo()) throw new Error("Nothing to redo."); }),
  };
  try {
    if (!options.readonly) detach = options.controller.attachRichEditor(instance, generation);
    syncInput();
    return instance;
  } catch (error) {
    await instance.destroy();
    throw error;
  }
}
