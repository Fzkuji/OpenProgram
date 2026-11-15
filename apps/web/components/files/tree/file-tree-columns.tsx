"use client";

import { forwardRef } from "react";
import { useTranslation } from "@/lib/i18n";

import { fileColumnGrid, fileColumnsWidth } from "../explorer/pierre-tree-theme";

const nameAndSizeGrid = "var(--op-files-name-width, 220px) 100px";

export function fileKind(path: string, type: "file" | "dir", text: (...variants: string[]) => string) {
  if (type === "dir") return text("Folder", "文件夹");
  const name = path.split("/").at(-1) ?? path;
  const dot = name.lastIndexOf(".");
  const extension = dot > 0 ? name.slice(dot + 1).toUpperCase() : "";
  return extension ? text(`${extension} file`, `${extension} 文件`) : text("File", "文件");
}

export const FileColumnsHeader = forwardRef<HTMLDivElement, { allColumns?: boolean; sort?: string; onSortChange?(value: string): void }>(function FileColumnsHeader({ allColumns = true, sort, onSortChange }, ref) {
  const { text } = useTranslation();
  const fields = sort?.split(":");
  const change = (key: string) => {
    if (!fields || !onSortChange) return;
    const direction = fields[0] === key ? (fields[1] === "asc" ? "desc" : "asc") : (key === "size" || key === "mtime" ? "desc" : "asc");
    onSortChange([key, direction, ...fields.slice(2)].join(":"));
  };
  return <div ref={ref} role="table" aria-label={text("File columns", "文件列")} data-file-columns-header style={{ flex: "none", overflow: "hidden", fontSize: "var(--right-panel-meta-size, var(--fs-2xs))", color: "var(--text-secondary)", borderBottom: "1px solid var(--border)", marginBottom: 6 }}>
    <div role="row" aria-label={text("File columns", "文件列")} style={{ width: fileColumnsWidth, margin: "0 6px", display: "grid", gridTemplateColumns: allColumns ? fileColumnGrid : nameAndSizeGrid, paddingRight: 6, boxSizing: "border-box", height: 30 }}>
      {[["name", text("Name", "名称")], ["size", text("Size", "大小")], ["mtime", text("Date Modified", "修改时间")], ["kind", text("Kind", "类型")]].slice(0, allColumns ? 4 : 2).map(([key, label]) => {
        const active = !!onSortChange && fields?.[0] === key;
        const content = <><span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>{active ? <span aria-hidden="true" style={{ fontSize: "var(--fs-2xs)" }}>{fields?.[1] === "asc" ? "⌃" : "⌄"}</span> : null}</>;
        const style = { display: "flex", alignItems: "center", justifyContent: key === "size" ? "flex-end" : "space-between", gap: 4, height: "100%", width: "100%", padding: `0 ${key === "size" && !allColumns ? 0 : 10}px 0 ${key === "name" ? 28 : 12}px`, boxSizing: "border-box" as const, textAlign: "left" as const };
        return <div key={key} role="columnheader" aria-label={label} aria-sort={active ? fields?.[1] === "asc" ? "ascending" : "descending" : undefined} style={{ minWidth: 0, borderRight: key === (allColumns ? "kind" : "size") ? undefined : "1px solid var(--border)" }}>
          {sort && onSortChange ? <button type="button" className="hover:bg-bg-hover focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent" onClick={() => change(key)} style={{ ...style, cursor: "pointer", color: active ? "var(--text-primary)" : "inherit", background: "transparent", border: 0, font: "inherit" }}>{content}</button> : <span style={style}>{content}</span>}
        </div>;
      })}
    </div>
  </div>;
});
