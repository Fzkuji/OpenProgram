"use client";

import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { FileTree, useFileTree } from "@pierre/trees/react";
import { pierreTreeIcons as icons, pierreTreeCSS as css } from "./pierre-tree-theme";
import { formatFileBytes, useFolderSize } from "./file-management";

export interface PierreTreeEntry { path: string; type: "file" | "dir"; size: number }
export interface PierreTreeHandle { reveal(path: string): boolean; scrollToTop(): void }
interface Props {
  projectId: string;
  entries: PierreTreeEntry[];
  expanded: Set<string>;
  selected: string | null;
  query?: string;
  matches?: ReadonlySet<string>;
  onRowsRendered?(paths: ReadonlySet<string>): void;
  onActivate?(path: string, type: "file" | "dir"): void;
  onExpandedChange(paths: Set<string>): void;
  onSelect(path: string, type: "file" | "dir"): void;
  onOpen(path: string): void;
  onContextMenu(event: React.MouseEvent<HTMLElement>, path: string, type: "file" | "dir"): void;
}
const normalize = (path: string) => path.replace(/\/$/, "");
const modelPath = (entry: PierreTreeEntry) => entry.path + (entry.type === "dir" ? "/" : "");

/** Pierre owns rows, focus and virtualization; the application owns filesystem state. */
export const PierreFileTree = forwardRef<PierreTreeHandle, Props>(function PierreFileTree(props, ref) {
  const latest = useRef(props);
  latest.current = props;
  const host = useRef<HTMLDivElement>(null);
  const syncing = useRef(false);
  const renderedPaths = useRef<ReadonlySet<string>>(new Set());
  useEffect(() => { props.onRowsRendered?.(renderedPaths.current); }, [props.onRowsRendered]);
  const sizes = useRef(new Map<string, { text: string; scanning: boolean }>());
  const [visibleFolders, setVisibleFolders] = useState<string[]>([]);
  const metadata = useRef(new Map<string, PierreTreeEntry>());
  metadata.current = new Map(props.entries.map(entry => [entry.path, entry]));
  const ranks = useRef(new Map<string, number>());
  ranks.current = new Map(props.entries.map((entry, index) => [entry.path, index]));
  const { model } = useFileTree({
    paths: props.entries.map(modelPath), flattenEmptyDirectories: false,
    sort: (left, right) => (ranks.current.get(normalize(left.path)) ?? 0) - (ranks.current.get(normalize(right.path)) ?? 0),
    initialExpandedPaths: [...props.expanded].map(path => path + "/"),
    itemHeight: 30, density: 1, icons, search: false, fileTreeSearchMode: "expand-matches", stickyFolders: true, unsafeCSS: css,
    onSelectionChange: paths => {
      if (syncing.current) return;
      const path = paths.at(-1);
      const entry = path == null ? undefined : metadata.current.get(normalize(path));
      if (entry) latest.current.onSelect(entry.path, entry.type);
    },
    renderRowDecoration: ({ item }) => {
      const entry = metadata.current.get(normalize(item.path));
      if (!entry) return null;
      const size = sizes.current.get(entry.path);
      const text = entry.type === "file" ? formatFileBytes(entry.size) : size?.text ?? "";
      const part = { text, color: entry.type === "dir" && size?.scanning ? "var(--op-size-scanning, var(--text-tertiary))" : undefined };
      return { text, parts: latest.current.matches?.has(entry.path) ? [{ text: "• ", color: "var(--trees-accent)" }, part] : [part] };
    },
  });
  useImperativeHandle(ref, () => ({
    reveal(path) {
      const entry = metadata.current.get(path);
      if (!entry) return false;
      model.scrollToPath(modelPath(entry), { offset: "center" });
      return true;
    },
    scrollToTop() { const first = latest.current.entries[0]; if (first) model.scrollToPath(modelPath(first), { offset: "top" }); },
  }), [model]);
  useEffect(() => {
    syncing.current = true;
    model.resetPaths(props.entries.map(modelPath), { initialExpandedPaths: [...props.expanded].map(path => path + "/") });
    // Initial expansion opens ancestors too; restore explicitly collapsed parents.
    for (const entry of props.entries) {
      if (entry.type !== "dir" || props.expanded.has(entry.path)) continue;
      const item = model.getItem(modelPath(entry));
      if (item && "collapse" in item) item.collapse();
    }
    for (const path of model.getSelectedPaths()) model.getItem(path)?.deselect();
    const selected = props.selected && metadata.current.get(props.selected);
    if (selected) model.getItem(modelPath(selected))?.select();
    syncing.current = false;
  }, [model, props.entries, props.expanded, props.selected]);
  useEffect(() => { model.setSearch(props.query || null); }, [model, props.query]);
  useEffect(() => { model.setIcons(icons); }, [model, props.matches]);
  useEffect(() => model.subscribe(() => {
    if (syncing.current) return;
    const next = new Set<string>();
    for (const entry of latest.current.entries) {
      if (entry.type !== "dir") continue;
      const item = model.getItem(modelPath(entry));
      if (item && "isExpanded" in item && item.isExpanded()) next.add(entry.path);
    }
    const old = latest.current.expanded;
    if (next.size !== old.size || [...next].some(path => !old.has(path))) latest.current.onExpandedChange(next);
  }), [model]);
  useEffect(() => {
    let frame = 0;
    let observer: MutationObserver | undefined;
    const connect = () => {
      const root = host.current?.querySelector("file-tree-container")?.shadowRoot;
      if (!root) { frame = requestAnimationFrame(connect); return; }
      // React mounts the Pierre host before its shadow renderer is ready.
      // Subscribe only after that renderer exists, then follow virtual rows.
      const update = () => {
        const paths = new Set<string>();
        const rendered = new Set<string>();
        for (const row of root.querySelectorAll("[data-item-path]")) {
          const path = normalize(row.getAttribute("data-item-path") ?? "");
          rendered.add(path);
          if (metadata.current.get(path)?.type === "dir") paths.add(path);
        }
        renderedPaths.current = rendered;
        latest.current.onRowsRendered?.(rendered);
        const next = [...paths];
        setVisibleFolders(old => old.join("\0") === next.join("\0") ? old : next);
      };
      observer = new MutationObserver(update);
      observer.observe(root, { childList: true, subtree: true });
      update();
    };
    connect();
    return () => { cancelAnimationFrame(frame); observer?.disconnect(); };
  }, [model]);
  const fromEvent = (event: React.SyntheticEvent) => {
    for (const target of event.nativeEvent.composedPath()) {
      if (!(target instanceof Element)) continue;
      const path = target.getAttribute("data-item-path");
      if (path) return metadata.current.get(normalize(path));
    }
  };
  return <div ref={host} style={{ height: "100%", minHeight: 0 }}>
    <FileTree model={model} style={{ height: "100%", display: "block" }}
      onClick={event => { const entry = fromEvent(event); if (entry) { if (props.onActivate) props.onActivate(entry.path, entry.type); else if (entry.type === "file") props.onOpen(entry.path); } }}
      onKeyDown={event => { if (event.key !== "Enter") return; const path = model.getFocusedPath(); const entry = path && metadata.current.get(normalize(path)); if (entry) { if (props.onActivate) props.onActivate(entry.path, entry.type); else if (entry.type === "file") props.onOpen(entry.path); } }}
      onContextMenu={event => { const entry = fromEvent(event); if (entry) props.onContextMenu(event, entry.path, entry.type); }} />
    {visibleFolders.map(path => <FolderSizeSubscription key={path} projectId={props.projectId} path={path} onValue={(text, scanning) => {
      const previous = sizes.current.get(path);
      if (previous?.text === text && previous.scanning === scanning) return;
      sizes.current.set(path, { text, scanning });
      // The public setter redraws decorations without resetting focus or scroll.
      model.setIcons(icons);
    }} />)}
  </div>;
});
function FolderSizeSubscription({ projectId, path, onValue }: { projectId: string; path: string; onValue(value: string, scanning: boolean): void }) {
  const { value } = useFolderSize(projectId, path, true);
  const display = value.bytes == null ? "" : formatFileBytes(value.bytes);
  const scanning = value.state === "scanning" && value.bytes != null;
  useEffect(() => onValue(display, scanning), [display, scanning, onValue]);
  return null;
}

export const PierreSearchTree = forwardRef<PierreTreeHandle, Pick<Props, "projectId" | "onSelect" | "onOpen" | "onContextMenu" | "onActivate" | "query"> & { matches: PierreTreeEntry[]; currentPath: string | null }>(function PierreSearchTree({ matches, currentPath, ...props }, ref) {
  const [expanded, setExpanded] = useState(new Set<string>());
  const [selected, setSelected] = useState<string | null>(null);
  const entries = useMemo(() => {
    const rows = new Map<string, PierreTreeEntry>();
    for (const entry of matches) {
      const parts = entry.path.split("/");
      for (let count = 1; count < parts.length; count++) {
        const path = parts.slice(0, count).join("/");
        if (!rows.has(path)) rows.set(path, { path, type: "dir", size: 0 });
      }
      rows.set(entry.path, entry);
    }
    return [...rows.values()];
  }, [matches]);
  useEffect(() => {
    setExpanded(new Set(entries.filter(entry => entry.type === "dir").map(entry => entry.path)));
  }, [entries]);
  return <PierreFileTree {...props} ref={ref} entries={entries} expanded={expanded} selected={currentPath ?? selected} matches={new Set(matches.map(entry => entry.path))}
    onExpandedChange={setExpanded} onSelect={(path, type) => { setSelected(path); props.onSelect(path, type); }} />;
});
