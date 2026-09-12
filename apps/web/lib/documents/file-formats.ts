/** Capabilities describe supported operations, never evidence of a valid file. */
export type FilePreviewKind = "text" | "image" | "layered-image" | "pdf" | "audio" | "video" | "office" | "download";
export type OfficeDocumentFormat = "docx" | "pptx" | "xlsx" | "odt" | "odp" | "ods";
export interface OfficeCapability { format: OfficeDocumentFormat; editable: boolean; conversionRequired: boolean; }
const textExtensions = new Set(["txt", "md", "mdx", "markdown", "json", "yaml", "yml", "toml", "ini", "cfg", "conf", "csv", "tsv", "js", "jsx", "mjs", "cjs", "ts", "tsx", "py", "rb", "go", "rs", "java", "kt", "swift", "c", "h", "cpp", "hpp", "sh", "bash", "zsh", "fish", "css", "scss", "html", "xml", "sql", "log"]);
export const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp", "avif"]);
const audioExtensions = new Set(["mp3", "wav", "ogg", "oga", "opus", "m4a", "aac", "flac"]);
const videoExtensions = new Set(["mp4", "m4v", "webm", "mov", "ogv"]);
const officeExtensions = new Set(["docx", "pptx", "xlsx", "odt", "odp", "ods"]);
const legacyOfficeExtensions = new Set(["doc", "ppt", "xls"]);
export function fileExtension(path: string): string {
  const name = path.replace(/\\/g, "/").split("/").pop() ?? "";
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}
export function fileCapabilities(path: string): { preview: FilePreviewKind; textEditable: boolean } {
  const ext = fileExtension(path);
  const name = path.replace(/\\/g, "/").split("/").pop() ?? "";
  let preview: FilePreviewKind = "download";
  if (textExtensions.has(ext) || ["Dockerfile", "Makefile", "LICENSE", "README", ".gitignore"].includes(name)) preview = "text";
  else if (IMAGE_EXTENSIONS.has(ext)) preview = "image";
  else if (["psd", "tif", "tiff"].includes(ext)) preview = "layered-image";
  else if (ext === "pdf") preview = "pdf";
  else if (audioExtensions.has(ext)) preview = "audio";
  else if (videoExtensions.has(ext)) preview = "video";
  else if (officeExtensions.has(ext) || legacyOfficeExtensions.has(ext)) preview = "office";
  return { preview, textEditable: preview === "text" };
}

export function officeCapability(path: string): OfficeCapability | null {
  const ext = fileExtension(path);
  if (officeExtensions.has(ext)) return { format: ext as OfficeDocumentFormat, editable: true, conversionRequired: false };
  if (legacyOfficeExtensions.has(ext)) {
    const format = ({ doc: "docx", ppt: "pptx", xls: "xlsx" } as const)[ext as "doc" | "ppt" | "xls"];
    return { format, editable: false, conversionRequired: true };
  }
  return null;
}
