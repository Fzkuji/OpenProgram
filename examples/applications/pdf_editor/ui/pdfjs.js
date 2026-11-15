// PDF.js core and the viewer components (PDFViewer, find controller, link
// service). The viewer build reads the core from globalThis.pdfjsLib, so the
// core is published there before the viewer module is evaluated.
import "./polyfills.js";
import * as pdfjs from "./vendor/pdfjs/pdf.min.mjs";
import * as pdfWorker from "./vendor/pdfjs/pdf.worker.min.mjs";

// The application frame has an opaque origin, which cannot start a Worker;
// PDF.js runs its parser on the main thread with the bundled worker module.
globalThis.pdfjsWorker = pdfWorker;
globalThis.pdfjsLib = pdfjs;
pdfjs.GlobalWorkerOptions.workerSrc = new URL("./vendor/pdfjs/pdf.worker.min.mjs", import.meta.url).href;

const viewer = await import("./vendor/pdfjs/web/pdf_viewer.mjs");

export { pdfjs, viewer };
export const ASSETS = {
  cMapUrl: new URL("./vendor/pdfjs/cmaps/", import.meta.url).href,
  standardFontDataUrl: new URL("./vendor/pdfjs/standard_fonts/", import.meta.url).href,
  wasmUrl: new URL("./vendor/pdfjs/wasm/", import.meta.url).href,
  imageResourcesPath: new URL("./vendor/pdfjs/web/images/", import.meta.url).href,
};
