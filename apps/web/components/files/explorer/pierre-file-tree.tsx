"use client";

import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { FileTree, useFileTree } from "@pierre/trees/react";
import { pierreTreeIcons as icons, pierreTreeCSS as css, fileColumnWidths } from "./pierre-tree-theme";
import { formatFileBytes, useFolderSize } from "../file-management";
import { useTranslation } from "@/lib/i18n";
import { FileColumnsHeader, fileKind } from "../tree/file-tree-columns";
import { carriesFiles } from "../tree/file-tree-drop";
import { attachOverlayScrollbar } from "@/lib/runtime-bridge/dom/scrollbar";
import styles from "../files-panel.module.css";

export interface PierreTreeEntry { path: string; type: "file" | "dir"; size: number; mtime?: number }
export interface PierreScrollState { path: string; offset: number }
export interface PierreTreeHandle {
  reveal(path: string): boolean;
  scrollToTop(): void;
  getScrollState(): PierreScrollState | null;
  restoreScrollState(state: PierreScrollState): boolean;
  /** Edit the row's name in place; false when the row is not in the tree. */
  startRename(path: string): boolean;
}
/** A new entry being named: a placeholder row inside ``dir``. */
export interface PierreTreeDraft { dir: string; kind: "file" | "dir" }
interface Props {
  /** The project whose folder sizes the Size column shows. */
  projectId?: string;
  /** False: a bare listing (Abilities), with no Size, Date Modified or Kind columns. */
  columns?: boolean;
  /** No renaming in place; with no ``movable``, ``draft`` or ``onContextMenu`` the tree only browses. */
  readOnly?: boolean;
  entries: PierreTreeEntry[];
  expanded: Set<string>;
  selected: string | null;
  /** Every selected row; the primary ``selected`` is among them. */
  selectedPaths?: readonly string[];
  /** Rows can be dragged onto folders; the drop is reported, not applied. */
  movable?: boolean;
  query?: string;
  sort?: string;
  onSortChange?(value: string): void;
  matches?: ReadonlySet<string>;
  onRowsRendered?(paths: ReadonlySet<string>): void;
  onActivate?(path: string, type: "file" | "dir"): void;
  onExpandedChange(paths: Set<string>): void;
  onSelect(path: string, type: "file" | "dir"): void;
  onSelectionChange?(entries: { path: string; type: "file" | "dir" }[]): void;
  onMove?(paths: string[], targetDir: string): void;
  /** Files dragged in from the desktop, over or onto ``targetDir``. */
  onExternalDrop?(transfer: DataTransfer, targetDir: string): void;
  onOpen?(path: string): void;
  onContextMenu?(event: React.MouseEvent<HTMLElement>, path: string, type: "file" | "dir"): void;
  /** Shows a placeholder row in ``draft.dir`` with its name being edited. */
  draft?: PierreTreeDraft | null;
  onDraftCommit?(name: string): void;
  onDraftCancel?(): void;
  onRenameCommit?(path: string, name: string): void;
}
const normalize = (path: string) => path.replace(/\/$/, "");
const modelPath = (entry: PierreTreeEntry) => entry.path + (entry.type === "dir" ? "/" : "");
const baseName = (path: string) => normalize(path).split("/").pop() ?? "";
/** The event came from Pierre's inline name editor. */
const renameInput = (event: React.SyntheticEvent) => event.nativeEvent.composedPath()
  .find((target): target is HTMLInputElement => target instanceof HTMLInputElement && target.hasAttribute("data-item-rename-input"));

/** Pierre owns rows, focus and virtualization; the application owns filesystem state. */
export const PierreFileTree = forwardRef<PierreTreeHandle, Props>(function PierreFileTree(props, ref) {
  const { text: translate, locale } = useTranslation();
  const dateFormat = useMemo(() => new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }), [locale]);
  const labels = useRef({ translate, dateFormat });
  labels.current = { translate, dateFormat };
  const header = useRef<HTMLDivElement>(null);
  const latest = useRef(props);
  latest.current = props;
  const host = useRef<HTMLDivElement>(null);
  const syncing = useRef(false);
  const renderedPaths = useRef<ReadonlySet<string>>(new Set());
  const { onRowsRendered } = props;
  useEffect(() => { onRowsRendered?.(renderedPaths.current); }, [onRowsRendered]);
  const sizes = useRef(new Map<string, { text: string; scanning: boolean }>());
  const [visibleFolders, setVisibleFolders] = useState<string[]>([]);
  const [allColumns, setAllColumns] = useState(false);
  const metadata = useRef(new Map<string, PierreTreeEntry>());
  metadata.current = new Map(props.entries.map(entry => [entry.path, entry]));
  // New entries are named in the tree itself, VS Code-style: a placeholder
  // row at the top of its folder opens in Pierre's own rename editor with
  // the name left empty. Enter or blur creates it under the typed name;
  // Escape, or leaving with nothing typed, drops it, so nothing is created
  // without a name and no nameless row is left behind.
  const { draft } = props;
  const draftEntry = useMemo(() => {
    if (!draft) return null;
    const base = draft.kind === "dir" ? translate("untitled folder", "未命名文件夹") : translate("untitled", "未命名");
    const taken = new Set(props.entries.map(entry => entry.path));
    const at = (name: string) => draft.dir ? `${draft.dir}/${name}` : name;
    let name = base;
    for (let count = 2; taken.has(at(name)); count++) name = `${base} ${count}`;
    return { path: at(name), type: draft.kind, size: 0 } satisfies PierreTreeEntry;
  }, [draft, props.entries, translate]);
  const drafting = useRef<PierreTreeEntry | null>(null);
  // What has been typed for the placeholder, kept across row resets.
  const draftName = useRef("");
  // Where focus was when the draft opened: a dropped draft hands it back
  // there, since Pierre would otherwise move it (and its ring) to the first row.
  const draftOpener = useRef<{ element: Element | null; path: string | null }>({ element: null, path: null });
  // A rename asked for before a pending reload lands is started again after it.
  const pendingRename = useRef<string | null>(null);
  const ranks = useRef(new Map<string, number>());
  ranks.current = new Map(props.entries.map((entry, index) => [entry.path, index]));
  if (draftEntry) ranks.current.set(draftEntry.path, -1);
  const { model } = useFileTree({
    paths: props.entries.map(modelPath), flattenEmptyDirectories: false,
    sort: (left, right) => (ranks.current.get(normalize(left.path)) ?? 0) - (ranks.current.get(normalize(right.path)) ?? 0),
    initialExpandedPaths: [...props.expanded].map(path => path + "/"),
    itemHeight: 30, density: 1, icons, search: false, fileTreeSearchMode: "expand-matches", stickyFolders: true, unsafeCSS: css,
    onSelectionChange: paths => {
      if (syncing.current) return;
      const entries = paths.map(path => metadata.current.get(normalize(path))).filter((entry): entry is PierreTreeEntry => Boolean(entry));
      latest.current.onSelectionChange?.(entries.map(({ path, type }) => ({ path, type })));
      const entry = entries.at(-1);
      if (entry) latest.current.onSelect(entry.path, entry.type);
    },
    // Pierre moves the rows at once; the server move follows, and a refusal
    // reloads the listing, so the tree never keeps a move the disk did not.
    dragAndDrop: props.movable ? {
      openOnDropDelay: 600,
      onDropComplete: event => {
        const dir = event.target.kind === "root" ? "" : normalize(event.target.directoryPath ?? "");
        latest.current.onMove?.(event.draggedPaths.map(normalize), dir);
      },
    } : false,
    renaming: props.readOnly ? false : {
      onRename: ({ sourcePath, destinationPath }) => {
        const path = normalize(sourcePath);
        pendingRename.current = null;
        if (drafting.current?.path === path) {
          drafting.current = null;
          latest.current.onDraftCommit?.(baseName(destinationPath));
        } else latest.current.onRenameCommit?.(path, baseName(destinationPath));
      },
    },
    renderRowDecoration: ({ item }) => {
      if (latest.current.columns === false) return null;
      const entry = metadata.current.get(normalize(item.path));
      if (!entry) return null;
      const size = sizes.current.get(entry.path);
      const text = entry.type === "file" ? formatFileBytes(entry.size) : size?.text ?? "";
      const part = { text: `${latest.current.matches?.has(entry.path) ? "• " : ""}${text}`, color: entry.type === "dir" && size?.scanning ? "var(--op-size-scanning, var(--text-secondary))" : undefined };
      const date = entry.mtime == null ? null : new Date(entry.mtime * 1000);
      const modified = date && Number.isFinite(date.getTime()) ? labels.current.dateFormat.format(date) : "";
      const kind = fileKind(entry.path, entry.type, labels.current.translate);
      return { text: [text, modified, kind].join(" · "), title: [text, modified, kind].filter(Boolean).join(" · "), parts: [part, { text: modified }, { text: kind }] };
    },
  });
  // Pierre exposes row indices but keeps its native scroll element in shadow DOM.
  const scrollElement = () => host.current?.querySelector("file-tree-container")?.shadowRoot
    ?.querySelector<HTMLElement>("[data-file-tree-virtualized-scroll]");
  useImperativeHandle(ref, () => ({
    getScrollState() {
      const element = scrollElement();
      if (!element) return null;
      const index = Math.floor(element.scrollTop / model.getItemHeight());
      const row = model.getVisibleRows(index, index)[0];
      return row ? { path: normalize(row.path), offset: element.scrollTop - index * model.getItemHeight() } : null;
    },
    restoreScrollState(state) {
      const element = scrollElement();
      if (!element || !Number.isFinite(state.offset)) return false;
      const count = model.getVisibleCount();
      for (let start = 0; start < count; start += 128) {
        const row = model.getVisibleRows(start, Math.min(start + 127, count - 1))
          .find(row => normalize(row.path) === state.path);
        if (!row) continue;
        const desired = Math.min(row.index * model.getItemHeight() + Math.max(0, Math.min(state.offset, model.getItemHeight() - 1)),
          Math.max(0, count * model.getItemHeight() - element.clientHeight));
        // The model can advance before its virtual list has committed its height.
        if (element.scrollHeight < desired + element.clientHeight) return false;
        element.scrollTop = desired;
        element.dispatchEvent(new Event("scroll"));
        return Math.abs(element.scrollTop - desired) < 1;
      }
      return false;
    },
    reveal(path) {
      const entry = metadata.current.get(path);
      if (!entry) return false;
      model.scrollToPath(modelPath(entry), { offset: "center" });
      return true;
    },
    scrollToTop() { const first = latest.current.entries[0]; if (first) model.scrollToPath(modelPath(first), { offset: "top" }); },
    startRename(path) {
      const entry = metadata.current.get(path);
      if (!entry) return false;
      pendingRename.current = modelPath(entry);
      if (!model.startRenaming(pendingRename.current)) return false;
      // Select the name without its extension, as Finder and VS Code do.
      const dot = entry.type === "file" ? baseName(entry.path).lastIndexOf(".") : -1;
      if (dot > 0) requestAnimationFrame(() => {
        host.current?.querySelector("file-tree-container")?.shadowRoot
          ?.querySelector<HTMLInputElement>("[data-item-rename-input]")?.setSelectionRange(0, dot);
      });
      return true;
    },
  }), [model]);
  const wantedSelection = (props.selectedPaths?.length ? props.selectedPaths : props.selected ? [props.selected] : []).join("\0");
  const applySelection = () => {
    const wanted = wantedSelection ? wantedSelection.split("\0") : [];
    const current = model.getSelectedPaths().map(normalize);
    if (current.length === wanted.length && current.every(path => wanted.includes(path))) return;
    for (const path of model.getSelectedPaths()) model.getItem(path)?.deselect();
    for (const path of wanted) {
      const entry = metadata.current.get(path);
      if (entry) model.getItem(modelPath(entry))?.select();
    }
  };
  useEffect(() => {
    syncing.current = true;
    const paths = props.entries.map(modelPath);
    if (draftEntry) paths.push(modelPath(draftEntry));
    model.resetPaths(paths, { initialExpandedPaths: [...props.expanded].map(path => path + "/") });
    // Initial expansion opens ancestors too; restore explicitly collapsed
    // parents. It also misses folders when the rows are not in Pierre's own
    // name order (Abilities lists tools before applications), so open those.
    for (const entry of props.entries) {
      if (entry.type !== "dir") continue;
      const item = model.getItem(modelPath(entry));
      if (!item || !("collapse" in item)) continue;
      if (!props.expanded.has(entry.path)) item.collapse();
      else if (!item.isExpanded()) item.expand();
    }
    for (const path of model.getSelectedPaths()) model.getItem(path)?.deselect();
    applySelection();
    syncing.current = false;
    if (drafting.current?.path !== draftEntry?.path) {
      draftName.current = "";
      if (draftEntry && !drafting.current) {
        const active = document.activeElement;
        draftOpener.current = { element: active && !host.current?.contains(active) ? active : null, path: model.getFocusedPath() };
      }
    }
    drafting.current = draftEntry;
    // Resetting the rows ends an open name editor; reopen the one in use.
    if (draftEntry) {
      model.startRenaming(modelPath(draftEntry), { removeIfCanceled: true });
      model.scrollToPath(modelPath(draftEntry), { offset: "center" });
      primeDraftEditor();
    } else if (pendingRename.current) model.startRenaming(pendingRename.current);
  // applySelection reads the selection key below; a selection change alone
  // must not reset the rows, or a Shift-click loses its anchor.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model, props.entries, props.expanded, draftEntry]);
  // Escape or an empty name removes the placeholder: the draft is dropped.
  useEffect(() => model.onMutation("remove", event => {
    if (drafting.current?.path !== normalize(event.path)) return;
    drafting.current = null;
    latest.current.onDraftCancel?.();
    const { element, path } = draftOpener.current;
    draftOpener.current = { element: null, path: null };
    requestAnimationFrame(() => {
      // A toolbar button gets focus back; a closed menu item cannot, so the
      // tree lets go of focus instead of ringing a row nobody chose.
      if (element instanceof HTMLElement && element.isConnected) element.focus({ preventScroll: true });
      else if (!element && path && model.getItem(path)) model.focusPath(path);
      else if (host.current?.contains(document.activeElement)) (document.activeElement as HTMLElement).blur();
    });
  }), [model]);
  // Pierre opens the editor on the placeholder's own name; replace it with
  // what was typed so far (nothing, at first), shown over a dim hint.
  function primeDraftEditor(attempt = 0) {
    requestAnimationFrame(() => {
      const entry = drafting.current;
      if (!entry) return;
      const input = host.current?.querySelector("file-tree-container")?.shadowRoot
        ?.querySelector<HTMLInputElement>("input[data-item-rename-input]");
      if (!input) { if (attempt < 10) primeDraftEditor(attempt + 1); return; }
      input.placeholder = baseName(entry.path);
      if (input.value === draftName.current) return;
      input.value = draftName.current;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
  // Pierre reports a rename only when the name changed, so keeping the
  // placeholder's own name creates it under that name.
  const keepDraftName = (input: HTMLInputElement) => {
    const entry = drafting.current;
    if (!entry || input.value.trim() !== baseName(entry.path)) return;
    drafting.current = null;
    latest.current.onDraftCommit?.(baseName(entry.path));
  };
  useEffect(() => {
    syncing.current = true;
    applySelection();
    syncing.current = false;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model, wantedSelection]);
  useEffect(() => { model.setSearch(props.query || null); }, [model, props.query]);
  useEffect(() => { model.setIcons(icons); }, [model, props.matches, locale]);
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
    let resize: ResizeObserver | undefined;
    let cleanupScroll: (() => void) | undefined;
    const connect = () => {
      const root = host.current?.querySelector("file-tree-container")?.shadowRoot;
      if (!root) { frame = requestAnimationFrame(connect); return; }
      const scroll = root.querySelector<HTMLElement>("[data-file-tree-virtualized-scroll]");
      const columns = header.current;
      const bare = latest.current.columns === false;
      if (!scroll || (!columns && !bare)) { frame = requestAnimationFrame(connect); return; }
      // The vertical bar is the app's overlay thumb, so rows run to the right
      // edge. Rows never outgrow the panel: Date Modified and Kind join Name
      // and Size only when all four fit.
      const measure = () => {
        const width = scroll.clientWidth;
        if (!width) return;
        const all = !bare && width >= 720;
        const meta = bare ? 0 : fileColumnWidths.size + fileColumnWidths.padding + (all ? fileColumnWidths.modified + fileColumnWidths.kind : 0);
        const rows = width - 2 * fileColumnWidths.inset;
        host.current?.style.setProperty("--op-files-name-width", `${bare ? rows : Math.max(120, rows - meta)}px`);
        host.current?.style.setProperty("--op-files-meta-width", `${meta}px`);
        host.current?.style.setProperty("--op-files-more-columns", all ? "block" : "none");
        host.current?.style.setProperty("--op-files-meta-columns", all ? "100px 200px 140px" : "100px");
        host.current?.style.setProperty("--op-files-size-end", all ? "10px" : "0px");
        if (columns) columns.style.width = `${width}px`;
        setAllColumns(all);
      };
      const detachThumb = attachOverlayScrollbar(scroll);
      cleanupScroll = detachThumb;
      resize = new ResizeObserver(measure);
      resize.observe(scroll);
      measure();
      // Subscribe only after the shadow renderer exists, then follow virtual rows.
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
    return () => { cancelAnimationFrame(frame); observer?.disconnect(); resize?.disconnect(); cleanupScroll?.(); };
  }, [model]);
  const fromEvent = (event: React.SyntheticEvent) => {
    for (const target of event.nativeEvent.composedPath()) {
      if (!(target instanceof Element)) continue;
      const path = target.getAttribute("data-item-path");
      if (path) return metadata.current.get(normalize(path));
    }
  };
  // A desktop drop lands in the folder under the pointer, the folder of
  // the file under it, or the project root.
  const [externalTarget, setExternalTarget] = useState<string | null>(null);
  const dropDir = (event: React.DragEvent) => {
    const entry = fromEvent(event);
    if (!entry) return "";
    return entry.type === "dir" ? entry.path : entry.path.split("/").slice(0, -1).join("/");
  };
  const sizeProject = props.projectId;
  const external = (event: React.DragEvent) => Boolean(props.onExternalDrop) && carriesFiles(event.dataTransfer);
  return <div ref={host} className={externalTarget !== null ? styles.externalDrop : undefined} style={{ height: "100%", minHeight: 0, minWidth: 0, display: "flex", flexDirection: "column", overflow: "hidden", position: "relative" }}
    onDragEnter={event => { if (!external(event)) return; event.preventDefault(); event.stopPropagation(); setExternalTarget(dropDir(event)); }}
    onDragOver={event => {
      if (!external(event)) return;
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = "copy";
      const dir = dropDir(event);
      setExternalTarget(old => old === dir ? old : dir);
    }}
    onDragLeave={event => {
      if (!external(event)) return;
      event.stopPropagation();
      const next = event.relatedTarget;
      if (next instanceof Node && host.current?.contains(next)) return;
      setExternalTarget(null);
    }}
    onDrop={event => {
      if (!external(event)) return;
      event.preventDefault();
      event.stopPropagation();
      setExternalTarget(null);
      props.onExternalDrop?.(event.dataTransfer, dropDir(event));
    }}>
    {props.columns === false ? null : <FileColumnsHeader ref={header} allColumns={allColumns} sort={props.sort} onSortChange={props.onSortChange} />}
    <FileTree model={model} style={{ flex: "1 1 0", minHeight: 0, display: "block" }}
      onClick={event => { const entry = fromEvent(event); if (entry) { if (props.onActivate) props.onActivate(entry.path, entry.type); else if (entry.type === "file") props.onOpen?.(entry.path); } }}
      onBlur={event => { const input = renameInput(event); if (input) { pendingRename.current = null; keepDraftName(input); } }}
      onInput={event => { const input = renameInput(event); if (input && drafting.current) draftName.current = input.value; }}
      onKeyDown={event => {
        const input = renameInput(event);
        if (input && (event.key === "Enter" || event.key === "Escape")) pendingRename.current = null;
        if (event.key !== "Enter") return;
        if (input) { keepDraftName(input); return; }
        const path = model.getFocusedPath();
        const entry = path && metadata.current.get(normalize(path));
        if (entry) { if (props.onActivate) props.onActivate(entry.path, entry.type); else if (entry.type === "file") props.onOpen?.(entry.path); }
      }}
      onContextMenu={event => { const entry = fromEvent(event); if (entry) props.onContextMenu?.(event, entry.path, entry.type); }} />
    {externalTarget !== null ? <div className={styles.externalDropHint}>{translate(`Add to ${externalTarget || "the project root"}`, `添加到 ${externalTarget || "项目根目录"}`)}</div> : null}
    {props.columns === false || !sizeProject ? null : visibleFolders.map(path => <FolderSizeSubscription key={path} projectId={sizeProject} path={path} onValue={(text, scanning) => {
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

export const PierreSearchTree = forwardRef<PierreTreeHandle, Pick<Props, "projectId" | "columns" | "readOnly" | "onSelect" | "onOpen" | "onContextMenu" | "onActivate" | "query"> & { matches: PierreTreeEntry[]; currentPath: string | null }>(function PierreSearchTree({ matches, currentPath, ...props }, ref) {
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
