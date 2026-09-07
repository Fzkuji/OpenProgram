"use client";

import { useId } from "react";
import { getBuiltInSpriteSheet, createFileTreeIconResolver } from "@pierre/trees";
import { pierreTreeIcons } from "./pierre-tree-theme";
import styles from "./file-type-icon.module.css";

const resolver = createFileTreeIconResolver(pierreTreeIcons);

// Only trusted, package SVG markup enters the renderer, never file content.
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
  const resolved = resolver.resolveIcon("file-tree-icon-file", basename);
  const token = resolved.token ?? "default";
  const markup = icons.get(resolved.name) ?? icons.get("file-tree-icon-file")!;
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
