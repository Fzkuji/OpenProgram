"use client";

/**
 * Floating menu for ``@`` mentions in the composer: open web tabs and
 * running apps (when the composer supplies them), then files, each group
 * under its own heading.
 *
 * Owns nothing of its own — the parent composer drives ``items``,
 * ``selectedIndex``, ``onPick``. Layout mirrors the slash menu (same
 * portal pattern, same row visuals via inline styles so it inherits
 * the page's CSS variables).
 */

import React from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "@/lib/i18n";
import { FileTypeIcon } from "@/components/files/file-type-icon";
import { SolarIcon } from "@/components/solar-icons";
import { TabFavicon } from "@/components/center-tabs/strip/tab-favicon";
import { FillIcon } from "@/components/ui/fill-icon";

import { mentionHost, mentionKey, type Mention } from "./mention-sources";

export type FileMatch = Mention;

function kindOf(item: Mention): "tab" | "app" | "file" {
  return item.kind ?? "file";
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
        // `bottom` anchors the menu above the field so it grows upward.
        ...(position.bottom !== undefined ? { bottom: position.bottom } : { top: position.top }),
        zIndex: 9999,
        minWidth: 260,
        maxWidth: 480,
        maxHeight: 320,
        overflowY: "auto",
        // Shared menu frame (ui/menu/menu-styles.ts MENU_PANEL): 4px padding.
        padding: 4,
        // Glass popover recipe (app/styles/themes/fallback.css --glass-*).
        background: "var(--glass-surface)",
        WebkitBackdropFilter: "var(--glass-backdrop)",
        backdropFilter: "var(--glass-backdrop)",
        border: "0.5px solid var(--glass-ring)",
        borderRadius: "var(--glass-radius)",
        boxShadow: "var(--glass-shadow)",
        fontSize: "var(--fs-xs)",
        lineHeight: "19px",
      }}
    >
      {loading && items.length === 0 ? (
        <div style={{ padding: "2.5px 8px", color: "var(--text-muted)" }}>
          {text("Searching...", "搜索中...")}
        </div>
      ) : items.length === 0 ? (
        <div style={{ padding: "2.5px 8px", color: "var(--text-muted)" }}>
          {query
            ? text(`Nothing matches "${query}"`, `没有匹配“${query}”的内容`)
            : text("Type to search files", "输入以搜索文件")}
        </div>
      ) : (
        items.map((item, i) => {
          const selected = i === selectedIndex;
          const kind = kindOf(item);
          const heading = kind !== kindOf(items[i - 1] ?? item) || i === 0
            ? { tab: text("Tabs", "标签页"), app: text("Running apps", "正在运行的应用"), file: text("Files", "文件") }[kind]
            : null;
          // Headings appear only once tabs or apps share the menu with files.
          const grouped = items.some((other) => kindOf(other) !== "file");
          return (
            <React.Fragment key={mentionKey(item)}>
            {grouped && heading && (
              <div
                role="presentation"
                style={{
                  padding: i === 0 ? "2px 8px" : "6px 8px 2px",
                  color: "var(--text-muted)",
                  fontFamily: "var(--font-sans)",
                  fontSize: "var(--fs-2xs)",
                  lineHeight: "16px",
                }}
              >
                {heading}
              </div>
            )}
            <div
              role="option"
              aria-selected={selected}
              onMouseEnter={() => onHover(i)}
              onClick={() => onPick(item)}
              // Shared menu row (itemCls): 24px, 2.5px 8px, 6px radius,
              // the keyboard pick tinted like hover.
              style={{
                minHeight: 24,
                padding: "2.5px 8px",
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
                background: selected ? "var(--bg-hover)" : "transparent",
                color: selected ? "var(--text-bright)" : "var(--text-primary)",
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
                  width: 16,
                  justifyContent: "center",
                  display: "inline-flex",
                  flexShrink: 0,
                  color: "var(--text-muted)",
                }}
              >
                {item.kind === "tab"
                  ? <FillIcon><TabFavicon url={item.faviconUrl} /></FillIcon>
                  : item.kind === "app"
                    ? item.icon
                      ? <FillIcon><img src={item.icon} alt="" width={16} height={16} style={{ display: "block" }} /></FillIcon>
                      : <SolarIcon name="monitor" size={16} fit="fill" />
                    : item.is_dir ? <SolarIcon name="folder-with-files" size={16} fit="fill" /> : <FillIcon><FileTypeIcon name={item.path} /></FillIcon>}
              </span>
              <span
                style={{
                  flex: 1,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  ...(item.kind === "tab" || item.kind === "app" ? { fontFamily: "var(--font-sans)" } : {}),
                }}
              >
                {item.kind === "tab" ? item.title : item.kind === "app" ? item.name : `${item.path}${item.is_dir ? "/" : ""}`}
              </span>
              {(item.kind === "tab" || item.kind === "app") && (
                <span style={{ flexShrink: 0, maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", color: "var(--text-muted)" }}>
                  {item.kind === "tab" ? mentionHost(item.url) : item.bundle_id}
                </span>
              )}
            </div>
            </React.Fragment>
          );
        })
      )}
    </div>
  );
  return createPortal(content, document.body);
}
