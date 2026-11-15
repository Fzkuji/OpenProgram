// Lazily rendered page thumbnails with a small render queue and cache.
import { pdfjs } from "./pdfjs.js";

const cache = new Map();   // key -> canvas (as data held in an ImageBitmap-free canvas)
const queue = [];
let running = 0;
let generation = 0;

export function resetThumbs() {
  generation++;
  cache.clear();
  queue.length = 0;
}

// Renders page `number` of `pdf` at `width` CSS pixels into a fresh canvas.
// `rotate` adds to the page's own rotation (for the organize view).
export function thumbnail(pdf, number, width, { key = "doc", rotate = 0 } = {}) {
  const id = `${key}:${number}:${Math.round(width)}:${rotate}`;
  if (cache.has(id)) return cache.get(id);
  const job = new Promise((resolve, reject) => queue.push({ pdf, number, width, rotate, resolve, reject, generation }));
  cache.set(id, job);
  pump();
  job.catch(() => cache.delete(id));
  return job;
}

function pump() {
  while (running < 2 && queue.length) {
    const task = queue.shift();
    if (task.generation !== generation) { task.reject(new Error("stale")); continue; }
    running++;
    render(task).then(task.resolve, task.reject).finally(() => { running--; pump(); });
  }
}

async function render({ pdf, number, width, rotate }) {
  const page = await pdf.getPage(number);
  const base = page.getViewport({ scale: 1, rotation: (page.rotate + rotate) % 360 });
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const scale = width / base.width;
  const viewport = page.getViewport({ scale: scale * ratio, rotation: (page.rotate + rotate) % 360 });
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.floor(viewport.width));
  canvas.height = Math.max(1, Math.floor(viewport.height));
  await page.render({
    canvasContext: canvas.getContext("2d"), viewport,
    annotationMode: pdfjs.AnnotationMode.ENABLE_STORAGE,
  }).promise;
  return canvas;
}

// Draws a cached thumbnail canvas into a container (a copy, so one cached
// render can appear in the sidebar and the organize grid at once).
export function place(container, source) {
  const canvas = document.createElement("canvas");
  canvas.width = source.width;
  canvas.height = source.height;
  canvas.getContext("2d").drawImage(source, 0, 0);
  container.querySelector("canvas")?.remove();
  container.prepend(canvas);
  container.classList.remove("pending");
}
