"use client";

/**
 * Paths in a reply's prose become links to the files they name.
 *
 * Once a reply has settled, its inline code spans and markdown links are
 * read for path-shaped strings (lib/files/prose-file-paths.ts), and the
 * worker is asked in one batched request which of them are files the
 * chat can open (lib/files/file-path-lookup.ts). Only those become links:
 * the text is drawn first and never waits for the answer. A click opens
 * the file in the preview beside the chat, a right-click opens the file
 * menu (components/files/file-path-menu.tsx). A markdown link that can
 * only be a local path (`/…`, `~/…`, `./…`, a drive) and is not confirmed
 * yet is looked up on click, so it never reaches the app's router as if it
 * were one of its pages; a bare one (`docs.example.ai`) keeps its own
 * behaviour until the worker confirms it.
 */
import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent, type ReactElement } from "react";

import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu";
import { FilePathMenuContent } from "@/components/files/file-path-menu";
import { useCurrentProject } from "@/lib/files/files-shared";
import { lookupFilePaths } from "@/lib/files/file-path-lookup";
import { openLocalFile } from "@/lib/files/open-local-file";
import { codeSpanPath, linkHrefPath, localPathHref } from "@/lib/files/prose-file-paths";
import { showToast } from "@/lib/format-utils/toast";
import { useTranslation } from "@/lib/i18n";

const LINK = "md-file-link";
const CHECKED = "fileChecked";
/** Path-shaped strings asked about per scan; a reply rarely names more. */
const MAX_CANDIDATES = 64;

interface Candidate { element: HTMLElement; path: string }

/** Code spans and links of the rendered prose not looked at yet. */
export function proseCandidates(root: ParentNode): Candidate[] {
  const found: Candidate[] = [];
  for (const element of root.querySelectorAll<HTMLElement>(".md-rendered code, .md-rendered a[href]")) {
    if (found.length >= MAX_CANDIDATES) break;
    if (element.dataset[CHECKED]) continue;
    element.dataset[CHECKED] = "1";
    if (element.tagName === "CODE") {
      if (element.closest("pre, a")) continue;
      const path = codeSpanPath(element.textContent ?? "");
      if (path) found.push({ element, path });
    } else {
      const path = linkHrefPath(element.getAttribute("href") ?? "");
      if (path) found.push({ element, path });
    }
  }
  return found;
}

/** Turn a confirmed candidate into a file link. A markdown link keeps its
 *  text and loses its address, so no path of it reaches the browser; a
 *  code span is wrapped, keeping its code look. */
export function markFileLink(element: HTMLElement, absolutePath: string): void {
  const link = element.tagName === "A" ? element : element.ownerDocument.createElement("a");
  if (link !== element) {
    element.replaceWith(link);
    link.append(element);
  }
  link.removeAttribute("href");
  link.classList.add(LINK);
  link.dataset.filePath = absolutePath;
  link.setAttribute("role", "link");
  link.tabIndex = 0;
}

function fileLinkAt(target: EventTarget | null): HTMLElement | null {
  return target instanceof Element ? target.closest<HTMLElement>(`.${LINK}[data-file-path]`) : null;
}

export function ProseFileLinks({ sessionId, settled, children }: {
  sessionId?: string;
  /** Scanned only once the reply has stopped streaming. */
  settled: boolean;
  children: ReactElement;
}) {
  const { text } = useTranslation();
  const project = useCurrentProject({ sessionId: sessionId ?? null, chatKey: null });
  const ref = useRef<HTMLElement>(null);
  const [menuPath, setMenuPath] = useState<string | null>(null);

  useEffect(() => {
    const root = ref.current;
    if (!settled || !sessionId || !root) return;
    let live = true;
    let frame = 0;
    const scan = () => {
      frame = 0;
      const found = proseCandidates(root);
      if (!found.length) return;
      void lookupFilePaths(sessionId, found.map(item => item.path)).then(answers => {
        if (!live) return;
        for (const { element, path } of found) {
          const file = answers.get(path);
          if (file && element.isConnected) markFileLink(element, file);
        }
      });
    };
    scan();
    // Prose drawn later (a block expanded, a reply re-rendered) is scanned
    // too; marking a link changes the tree only where it was scanned.
    const observer = new MutationObserver(() => { if (!frame) frame = requestAnimationFrame(scan); });
    observer.observe(root, { childList: true, subtree: true });
    return () => { live = false; observer.disconnect(); if (frame) cancelAnimationFrame(frame); };
  }, [settled, sessionId]);

  const open = (path: string) => { if (sessionId) openLocalFile(path, sessionId, project); };

  const onClick = (event: MouseEvent) => {
    const link = fileLinkAt(event.target);
    if (link) {
      event.preventDefault();
      open(link.dataset.filePath!);
      return;
    }
    const anchor = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>(".md-rendered a[href]") : null;
    // Only a link that can name nothing but a local file is held back; a
    // bare one (`docs.example.ai`) not confirmed as a file stays a link.
    const path = anchor ? localPathHref(anchor.getAttribute("href") ?? "") : null;
    if (!anchor || !path) return;
    event.preventDefault();
    if (!sessionId) return;
    void lookupFilePaths(sessionId, [path]).then(answers => {
      const file = answers.get(path);
      if (file) open(file);
      else showToast(text("This file isn't available.", "该文件不可用。"), { tone: "error" });
    });
  };
  const onKeyDown = (event: KeyboardEvent) => {
    const link = event.key === "Enter" ? fileLinkAt(event.target) : null;
    if (!link) return;
    event.preventDefault();
    open(link.dataset.filePath!);
  };
  // The menu is enabled only for a right-click (or the menu key) on a
  // file link, so everywhere else the reply keeps the browser's own menu
  // and a file card inside it keeps its own.
  const onPointerDownCapture = (event: PointerEvent) => {
    if (event.button === 2 || event.ctrlKey) setMenuPath(fileLinkAt(event.target)?.dataset.filePath ?? null);
  };
  const onKeyDownCapture = (event: KeyboardEvent) => {
    if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
      setMenuPath(fileLinkAt(event.target)?.dataset.filePath ?? null);
    }
  };

  return (
    <ContextMenu modal={false}>
      <ContextMenuTrigger
        asChild
        ref={ref}
        disabled={!menuPath}
        onClick={onClick}
        onKeyDown={onKeyDown}
        onPointerDownCapture={onPointerDownCapture}
        onKeyDownCapture={onKeyDownCapture}
      >
        {children}
      </ContextMenuTrigger>
      {menuPath ? (
        <FilePathMenuContent path={menuPath} sessionId={sessionId} project={project} onOpen={() => open(menuPath)} />
      ) : null}
    </ContextMenu>
  );
}
