/** Capabilities describe supported operations, never evidence of a valid file. */
export type FilePreviewKind = "text" | "image" | "layered-image" | "pdf" | "audio" | "video" | "download";
const textExtensions = new Set(["txt", "md", "mdx", "markdown", "json", "yaml", "yml", "toml", "ini", "cfg", "conf", "csv", "tsv", "js", "jsx", "mjs", "cjs", "ts", "tsx", "py", "rb", "go", "rs", "java", "kt", "swift", "c", "h", "cpp", "hpp", "sh", "bash", "zsh", "fish", "css", "scss", "html", "xml", "sql", "log"]);
export const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp", "avif"]);
const audioExtensions = new Set(["mp3", "wav", "ogg", "oga", "opus", "m4a", "aac", "flac"]);
const videoExtensions = new Set(["mp4", "m4v", "webm", "mov", "ogv"]);
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
  return { preview, textEditable: preview === "text" };
}
