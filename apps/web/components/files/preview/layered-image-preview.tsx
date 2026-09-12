"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "@/lib/i18n";

import { readPreviewBytes } from "@/lib/documents/read-preview-bytes";
const MAX_PIXELS = 16_000_000;

type PreviewData = { width: number; height: number; pixels: Uint8ClampedArray; layers: string[]; label: string };

function errorCode(code: "UNSUPPORTED_IMAGE" | "IMAGE_RESOURCE_LIMIT" | "CORRUPT_IMAGE") {
  return code;
}

function imagePixels(value: unknown, width: number, height: number): Uint8ClampedArray {
  const candidate = value && typeof value === "object" && "data" in value ? (value as { data: unknown }).data : value;
  if (!candidate || typeof (candidate as { length?: unknown }).length !== "number") throw new Error(errorCode("CORRUPT_IMAGE"));
  const pixels = new Uint8ClampedArray(candidate as ArrayLike<number>);
  if (pixels.length !== width * height * 4) throw new Error(errorCode("CORRUPT_IMAGE"));
  return pixels;
}

function psdHeader(buffer: ArrayBuffer) {
  const bytes = new Uint8Array(buffer);
  if (bytes.length < 26 || String.fromCharCode(...bytes.slice(0, 4)) !== "8BPS") throw new Error(errorCode("UNSUPPORTED_IMAGE"));
  const view = new DataView(buffer);
  return { channels: view.getUint16(12), height: view.getUint32(14), width: view.getUint32(18), depth: view.getUint16(22), mode: view.getUint16(24) };
}

function tiffNumber(ifd: Record<string, unknown>, key: string): number {
  const value = ifd[key];
  const n = Array.isArray(value) ? value[0] : value;
  return typeof n === "number" && Number.isFinite(n) ? n : 0;
}

async function decode(buffer: ArrayBuffer, path: string): Promise<PreviewData> {
  const ext = path.split(".").pop()?.toLowerCase();
  if (ext === "psd") {
    const header = psdHeader(buffer);
    if (header.width < 1 || header.height < 1 || header.width * header.height > MAX_PIXELS || header.depth !== 8 || header.mode !== 3 || (header.channels !== 3 && header.channels !== 4)) throw new Error(errorCode("UNSUPPORTED_IMAGE"));
    const mod = await import("ag-psd");
    const psd = (mod as unknown as { default?: typeof mod }).default ?? mod;
    const parsed = psd.readPsd(buffer, { skipThumbnail: true, skipLayerImageData: true, useImageData: true });
    const pixels = imagePixels(parsed.imageData, header.width, header.height);
    const layers: string[] = [];
    const visit = (items: unknown) => {
      if (!Array.isArray(items)) return;
      for (const item of items) {
        if (!item || typeof item !== "object") continue;
        const layer = item as { name?: unknown; children?: unknown };
        if (typeof layer.name === "string" && layer.name.trim()) layers.push(layer.name);
        visit(layer.children);
      }
    };
    visit(parsed.children);
    return { width: header.width, height: header.height, pixels, layers, label: "PSD composite" };
  }
  if (ext === "tif" || ext === "tiff") {
    // Keep UTIF lazy and constrain the small public surface used here.
    const mod = await import("utif");
    const UTIF = (mod as unknown as { default?: {
      decode: (value: ArrayBuffer) => Array<Record<string, unknown>>;
      decodeImage: (value: ArrayBuffer, ifd: Record<string, unknown>) => void;
      toRGBA8: (ifd: Record<string, unknown>) => ArrayLike<number>;
    } }).default ?? mod as unknown as {
      decode: (value: ArrayBuffer) => Array<Record<string, unknown>>;
      decodeImage: (value: ArrayBuffer, ifd: Record<string, unknown>) => void;
      toRGBA8: (ifd: Record<string, unknown>) => ArrayLike<number>;
    };
    const ifds = UTIF.decode(buffer) as Array<Record<string, unknown>>;
    const first = ifds?.[0];
    const width = tiffNumber(first ?? {}, "t256");
    const height = tiffNumber(first ?? {}, "t257");
    const bits = tiffNumber(first ?? {}, "t258");
    const samples = tiffNumber(first ?? {}, "t277");
    if (!first || width < 1 || height < 1 || width * height > MAX_PIXELS || bits !== 8 || (samples !== 0 && samples > 4)) throw new Error(errorCode("UNSUPPORTED_IMAGE"));
    UTIF.decodeImage(buffer, first);
    const pixels = imagePixels(UTIF.toRGBA8(first), width, height);
    return { width, height, pixels, layers: [], label: "TIFF first page" };
  }
  throw new Error(errorCode("UNSUPPORTED_IMAGE"));
}

export function LayeredImagePreview({ sourceUrl, path }: { sourceUrl: string; path: string }) {
  const { text } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [data, setData] = useState<PreviewData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setData(null);
    setError(null);
    (async () => {
      try {
        const bytes = await readPreviewBytes(sourceUrl, controller.signal);
        if (!active) return;
        const buffer = bytes.buffer as ArrayBuffer;
        const decoded = await decode(buffer, path);
        if (active) setData(decoded);
      } catch (cause) {
        if (!active || controller.signal.aborted) return;
        const code = cause instanceof Error && ["UNSUPPORTED_IMAGE", "PREVIEW_RESOURCE_LIMIT", "CORRUPT_IMAGE"].includes(cause.message) ? cause.message : "CORRUPT_IMAGE";
        setError(code);
      }
    })();
    return () => { active = false; controller.abort(); };
  }, [sourceUrl, path]);

  useEffect(() => {
    if (!data || !canvasRef.current) return;
    const canvas = canvasRef.current;
    canvas.width = data.width;
    canvas.height = data.height;
    const context = canvas.getContext("2d");
    if (!context) { setError("CORRUPT_IMAGE"); return; }
    context.putImageData(new ImageData(data.pixels as unknown as ImageDataArray, data.width, data.height), 0, 0);
    return () => { canvas.width = 0; canvas.height = 0; };
  }, [data]);

  if (error) return <div role="alert" style={{ padding: 16 }}><p>{text("This image cannot be previewed.", "无法预览此图片。")}</p><p>{error === "PREVIEW_RESOURCE_LIMIT" ? text("The file exceeds the 64 MiB preview limit.", "文件超过 64 MiB 预览限制。") : error === "UNSUPPORTED_IMAGE" ? text("This image uses unsupported color settings or exceeds 16 million pixels.", "图片颜色设置不受支持，或超过 1600 万像素。") : text("The file could not be read or decoded.", "无法读取或解码文件。")}</p><p><a href={sourceUrl} download={path.split("/").pop()}>{text("Download original", "下载原文件")}</a></p></div>;
  if (!data) return <div role="status" style={{ padding: 16 }}>{text("Loading preview…", "正在加载预览…")}</div>;
  return <div style={{ overflow: "auto", padding: 16 }}><p>{data.label === "PSD composite" ? text("PSD composite", "PSD 合成图") : text("TIFF first page", "TIFF 首页")} · {data.width} × {data.height}</p><canvas ref={canvasRef} aria-label={path} style={{ maxWidth: "100%", height: "auto", imageRendering: "auto" }} />{data.layers.length ? <div><p>{text("Layers", "图层")}</p><ul>{data.layers.map((layer, index) => <li key={`${layer}-${index}`}>{layer}</li>)}</ul></div> : null}</div>;
}

export default LayeredImagePreview;
