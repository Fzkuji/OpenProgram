"use client";

import { useId } from "react";
import { getBuiltInSpriteSheet, resolveBuiltInFileIconToken } from "./pierre/built-in-icons";
import styles from "./file-type-icon.module.css";

// Only trusted, vendored SVG markup enters the renderer, never file content.
const icons = new Map(Array.from(
  getBuiltInSpriteSheet("complete").matchAll(/<symbol id="([^"]+)"[^>]*>([\s\S]*?)<\/symbol>/g),
  ([, id, markup]) => [id, markup],
));

/** Shared decorative file identity; callers retain the visible filename. */
export function FileTypeIcon({ name, size = 16, className }: {
  name: string;
  size?: number;
  className?: string;
}) {
  const id = useId().replace(/:/g, "");
  const basename = name.split(/[\\/]/).pop()?.toLowerCase() ?? "";
  // Upstream maps use plain objects: exclude inherited keys before lookup.
  const safeName = Object.hasOwn(Object.prototype, basename) ? "" : basename;
  const extensions = safeName.split(".").slice(1)
    .map((_, index, parts) => parts.slice(index).join("."))
    .filter(extension => !Object.hasOwn(Object.prototype, extension));
  const token = resolveBuiltInFileIconToken("complete", safeName, extensions) ?? "default";
  const markup = icons.get(`file-tree-builtin-${token}`) ?? icons.get("file-tree-icon-file")!;
  return <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 16 16"
    width={size}
    height={size}
    className={[styles.icon, className].filter(Boolean).join(" ")}
    data-file-icon={token}
    aria-hidden="true"
    focusable="false"
    style={{ flexShrink: 0 }}
    dangerouslySetInnerHTML={{ __html: markup.replace(/id="([^"]+)"/g, `id="${id}-$1"`).replace(/url\(#([^)]+)\)/g, `url(#${id}-$1)`) }}
  />;
}
