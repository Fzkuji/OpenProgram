"use client";

/**
 * Floating menu for ``@file`` mentions in the composer.
 *
 * Owns nothing of its own — the parent composer drives ``items``,
 * ``selectedIndex``, ``onPick``. Layout mirrors the slash menu (same
 * portal pattern, same row visuals via inline styles so it inherits
 * the page's CSS variables).
 */

import React from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "@/lib/i18n";
import { FileTextIcon, FoldersIcon } from "@/components/animated-icons";

export interface FileMatch {
  path: string;
  is_dir: boolean;
}

interface FileMenuProps {
  items: FileMatch[];
  selectedIndex: number;
  position: { left: number; top: number; bottom?: number } | null;
  onHover: (idx: number) => void;
  onPick: (item: FileMatch) => void;
  loading: boolean;
  query: string;
}

export function FileMenu({
  items, selectedIndex, position, onHover, onPick, loading, query,
}: FileMenuProps) {
  const { text } = useTranslation();
  if (!position || typeof document === "undefined") return null;
  const content = (
    <div
      role="listbox"
      onMouseDown={(e) => e.preventDefault()}
      style={{
        position: "fixed",
        left: position.left,
        top: position.top,
        zIndex: 9999,
        minWidth: 260,
        maxWidth: 480,
        maxHeight: 320,
        overflowY: "auto",
        background: "var(--bg-secondary)",
        border: "1px solid var(--border)",
        borderRadius: 6,
        boxShadow: "var(--shadow-popover)",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        fontSize: 12,
      }}
    >
      {loading && items.length === 0 ? (
        <div style={{ padding: "6px 10px", color: "var(--text-muted)" }}>
          {text("Searching...", "搜索中...")}
        </div>
      ) : items.length === 0 ? (
        <div style={{ padding: "6px 10px", color: "var(--text-muted)" }}>
          {query
            ? text(`No files match "${query}"`, `没有匹配“${query}”的文件`)
            : text("Type to search files", "输入以搜索文件")}
        </div>
      ) : (
        items.map((item, i) => {
          const selected = i === selectedIndex;
          return (
            <div
              key={item.path}
              role="option"
              aria-selected={selected}
              onMouseEnter={() => onHover(i)}
              onClick={() => onPick(item)}
              style={{
                padding: "4px 10px",
                cursor: "pointer",
                background: selected ? "var(--bg-tertiary)" : "transparent",
                color: selected ? "var(--text-primary)" : "var(--text-secondary)",
                display: "flex",
                alignItems: "center",
                gap: 6,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              <span
                aria-hidden
                style={{
                  width: 12,
                  display: "inline-flex",
                  flexShrink: 0,
                  color: "var(--text-muted)",
                }}
              >
                {item.is_dir ? <FoldersIcon size={12} /> : <FileTextIcon size={12} />}
              </span>
              <span
                style={{
                  flex: 1,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {item.path}{item.is_dir ? "/" : ""}
              </span>
            </div>
          );
        })
      )}
    </div>
  );
  return createPortal(content, document.body);
}
