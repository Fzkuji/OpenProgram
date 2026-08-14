/**
 * Bookmarks are a FOLDER TREE (Chrome's bookmark manager), persisted as
 * `{version, root}` under one localStorage key. The pre-tree format was
 * a bare `Bookmark[]`; readBookmarkTree() migrates that array into the
 * root folder on first read, so nobody loses bookmarks on upgrade.
 *
 * The flat API (readBookmarks / toggleBookmark / removeBookmark /
 * renameBookmark / isBookmarked) still works and is what most callers
 * use — it flattens the tree in document order and appends new
 * bookmarks to the root. The tree functions below are the manager UI's
 * vocabulary: each takes the stored tree, returns a new one, and saves.
 */

export interface Bookmark {
  title: string;
  url: string;
  faviconUrl?: string;
}

export interface BookmarkNodeBase {
  id: string;
  title: string;
}
export interface BookmarkLeaf extends BookmarkNodeBase {
  kind: "bookmark";
  url: string;
  faviconUrl?: string;
}
export interface BookmarkFolder extends BookmarkNodeBase {
  kind: "folder";
  children: BookmarkNode[];
}
export type BookmarkNode = BookmarkLeaf | BookmarkFolder;

export interface ImportedBookmarkLeaf {
  kind: "bookmark";
  title: string;
  url: string;
  faviconUrl?: string;
}
export interface ImportedBookmarkFolder {
  kind: "folder";
  title: string;
  children: ImportedBookmarkNode[];
}
export type ImportedBookmarkNode = ImportedBookmarkLeaf | ImportedBookmarkFolder;

export const BOOKMARKS_STORAGE_KEY = "openprogram.bookmarks";
export const BOOKMARKS_CHANGE_EVENT = "openprogram:bookmarks-changed";
export const BOOKMARKS_VERSION = 2;
export const BOOKMARKS_ROOT_ID = "root";

export function subscribeBookmarks(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};

  const onStorage = (event: StorageEvent) => {
    if (event.key === BOOKMARKS_STORAGE_KEY) listener();
  };

  window.addEventListener(BOOKMARKS_CHANGE_EVENT, listener);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(BOOKMARKS_CHANGE_EVENT, listener);
    window.removeEventListener("storage", onStorage);
  };
}

// ponytail: counter + timestamp, no uuid dependency. Ids only need to be
// unique within one profile's tree.
let idCounter = 0;
function newId(prefix: string): string {
  idCounter += 1;
  return `${prefix}-${Date.now().toString(36)}-${idCounter.toString(36)}`;
}

function emptyRoot(): BookmarkFolder {
  return { kind: "folder", id: BOOKMARKS_ROOT_ID, title: "", children: [] };
}

/** Accepts both formats; unknown shapes degrade to an empty root rather
 *  than throwing, same as the old readBookmarks(). */
function parseNode(value: unknown): BookmarkNode | null {
  if (!value || typeof value !== "object") return null;
  const node = value as Record<string, unknown>;
  if (typeof node.title !== "string") return null;
  const id = typeof node.id === "string" ? node.id : newId("b");
  if (node.kind === "folder" || Array.isArray(node.children)) {
    const children = Array.isArray(node.children)
      ? node.children.map(parseNode).filter((child): child is BookmarkNode => child !== null)
      : [];
    return { kind: "folder", id, title: node.title, children };
  }
  if (typeof node.url !== "string") return null;
  const leaf: BookmarkLeaf = { kind: "bookmark", id, title: node.title, url: node.url };
  if (typeof node.faviconUrl === "string") leaf.faviconUrl = node.faviconUrl;
  return leaf;
}

export function readBookmarkTree(): BookmarkFolder {
  if (typeof window === "undefined") return emptyRoot();
  let parsed: unknown;
  try {
    parsed = JSON.parse(localStorage.getItem(BOOKMARKS_STORAGE_KEY) || "null");
  } catch {
    return emptyRoot();
  }
  // v1 (flat array) → migrate into the root folder.
  if (Array.isArray(parsed)) {
    const root = emptyRoot();
    root.children = parsed
      .map(parseNode)
      .filter((node): node is BookmarkNode => node !== null && node.kind === "bookmark");
    return root;
  }
  if (parsed && typeof parsed === "object") {
    const root = parseNode((parsed as { root?: unknown }).root);
    if (root && root.kind === "folder") return { ...root, id: BOOKMARKS_ROOT_ID };
  }
  return emptyRoot();
}

function persistTree(root: BookmarkFolder): boolean {
  try {
    localStorage.setItem(
      BOOKMARKS_STORAGE_KEY,
      JSON.stringify({ version: BOOKMARKS_VERSION, root }),
    );
  } catch {
    return false;
  }
  window.dispatchEvent(new Event(BOOKMARKS_CHANGE_EVENT));
  return true;
}

function saveTree(root: BookmarkFolder): BookmarkFolder {
  return persistTree(root) ? root : readBookmarkTree();
}

/** Depth-first list of every bookmark in the tree, document order. */
export function flattenBookmarks(folder: BookmarkFolder): Bookmark[] {
  const out: Bookmark[] = [];
  const walk = (node: BookmarkNode) => {
    if (node.kind === "folder") {
      node.children.forEach(walk);
      return;
    }
    out.push(
      node.faviconUrl
        ? { title: node.title, url: node.url, faviconUrl: node.faviconUrl }
        : { title: node.title, url: node.url },
    );
  };
  folder.children.forEach(walk);
  return out;
}

/** Rebuild a folder by mapping every node through `fn`; returning null
 *  drops the node (and, for a folder, its whole subtree). */
function mapTree(
  folder: BookmarkFolder,
  fn: (node: BookmarkNode) => BookmarkNode | null,
): BookmarkFolder {
  const children: BookmarkNode[] = [];
  for (const child of folder.children) {
    const mapped = fn(child);
    if (!mapped) continue;
    children.push(mapped.kind === "folder" ? mapTree(mapped, fn) : mapped);
  }
  return { ...folder, children };
}

export function findNode(folder: BookmarkFolder, id: string): BookmarkNode | null {
  if (folder.id === id) return folder;
  for (const child of folder.children) {
    if (child.id === id) return child;
    if (child.kind === "folder") {
      const found = findNode(child, id);
      if (found) return found;
    }
  }
  return null;
}

// ---- Tree operations ------------------------------------------------

export function createFolder(title: string, parentId = BOOKMARKS_ROOT_ID): BookmarkFolder {
  const root = readBookmarkTree();
  const parent = findNode(root, parentId);
  if (!parent || parent.kind !== "folder") return root;
  const folder: BookmarkFolder = {
    kind: "folder",
    id: newId("f"),
    title: title.trim() || "New folder",
    children: [],
  };
  return saveTree(insertInto(root, parentId, folder, -1));
}

export function renameNode(id: string, title: string): BookmarkFolder {
  const root = readBookmarkTree();
  const node = findNode(root, id);
  if (!node || id === BOOKMARKS_ROOT_ID) return root;
  // Empty title falls back to the url for a bookmark (matching
  // renameBookmark), or keeps the old name for a folder.
  const next = title.trim() || (node.kind === "bookmark" ? node.url : node.title);
  return saveTree(mapTree(root, (child) => (child.id === id ? { ...child, title: next } : child)));
}

export function deleteNode(id: string): BookmarkFolder {
  const root = readBookmarkTree();
  if (id === BOOKMARKS_ROOT_ID || !findNode(root, id)) return root;
  return saveTree(mapTree(root, (child) => (child.id === id ? null : child)));
}

function insertInto(
  folder: BookmarkFolder,
  parentId: string,
  node: BookmarkNode,
  index: number,
): BookmarkFolder {
  if (folder.id === parentId) {
    const children = [...folder.children];
    children.splice(index < 0 || index > children.length ? children.length : index, 0, node);
    return { ...folder, children };
  }
  return {
    ...folder,
    children: folder.children.map((child) =>
      child.kind === "folder" ? insertInto(child, parentId, node, index) : child,
    ),
  };
}

function containsNode(folder: BookmarkFolder, id: string): boolean {
  return folder.children.some(
    (child) => child.id === id || (child.kind === "folder" && containsNode(child, id)),
  );
}

/**
 * Move `id` into `parentId` at `index` (-1 = append). Refuses to move a
 * folder into itself or its own subtree — that would detach the subtree
 * from the root and silently lose every bookmark under it.
 */
export function moveNode(id: string, parentId: string, index = -1): BookmarkFolder {
  const root = readBookmarkTree();
  const node = findNode(root, id);
  if (!node || id === BOOKMARKS_ROOT_ID) return root;
  const parent = findNode(root, parentId);
  if (!parent || parent.kind !== "folder") return root;
  if (node.kind === "folder" && (parentId === id || containsNode(node, parentId))) return root;
  const detached = mapTree(root, (child) => (child.id === id ? null : child));
  return saveTree(insertInto(detached, parentId, node, index));
}

// ---- Flat API (unchanged surface for existing callers) --------------

export function readBookmarks(): Bookmark[] {
  return flattenBookmarks(readBookmarkTree());
}

export function toggleBookmark(bookmark: Bookmark): Bookmark[] {
  const root = readBookmarkTree();
  const existing = flattenBookmarks(root).some((item) => item.url === bookmark.url);
  if (existing) return removeBookmark(bookmark.url);
  const leaf: BookmarkLeaf = { kind: "bookmark", id: newId("b"), ...bookmark };
  return flattenBookmarks(saveTree(insertInto(root, BOOKMARKS_ROOT_ID, leaf, -1)));
}

export function removeBookmark(url: string): Bookmark[] {
  const root = readBookmarkTree();
  return flattenBookmarks(
    saveTree(mapTree(root, (child) => (child.kind === "bookmark" && child.url === url ? null : child))),
  );
}

export function renameBookmark(url: string, title: string): Bookmark[] {
  const root = readBookmarkTree();
  // Renaming a url that is not bookmarked is a no-op — no write, no
  // change event, matching the pre-tree behaviour.
  if (!flattenBookmarks(root).some((bookmark) => bookmark.url === url)) {
    return flattenBookmarks(root);
  }
  const next = mapTree(root, (child) =>
    child.kind === "bookmark" && child.url === url
      ? { ...child, title: title.trim() || child.url }
      : child,
  );
  return flattenBookmarks(saveTree(next));
}

export function isBookmarked(url: string): boolean {
  return readBookmarks().some((bookmark) => bookmark.url === url);
}

function canonicalHttpUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

/** Merge a browser's folder tree into the manager tree. Folder titles are
 * matched by sibling occurrence; bookmark URLs are deduplicated globally. */
export function importBookmarkTree(
  values: ImportedBookmarkNode[],
  legacyFolderTitle?: string,
): number {
  const root = readBookmarkTree();
  const existingUrls = new Set(
    flattenBookmarks(root)
      .map((item) => canonicalHttpUrl(item.url))
      .filter((url): url is string => url !== null),
  );
  const legacyFolderCandidate = legacyFolderTitle
    ? root.children.find(
      (node): node is BookmarkFolder => node.kind === "folder" && node.title === legacyFolderTitle,
    )
    : undefined;
  // Only the previous importer's flat folder is safe to reorganize. A
  // same-title folder with nested user content is an ordinary bookmark tree.
  const legacyFolder = legacyFolderCandidate
    && legacyFolderCandidate.children.length > 0
    && legacyFolderCandidate.children.every((child) => child.kind === "bookmark")
    ? legacyFolderCandidate
    : undefined;
  const legacyByUrl = new Map<string, { node: BookmarkLeaf; parent: BookmarkFolder }>();
  if (legacyFolder) {
    for (const child of legacyFolder.children) {
      if (child.kind !== "bookmark") continue;
      const url = canonicalHttpUrl(child.url);
      if (url) legacyByUrl.set(url, { node: child, parent: legacyFolder });
    }
  }
  let imported = 0;
  let changed = false;

  const merge = (target: BookmarkFolder, incoming: ImportedBookmarkNode[]) => {
    const folderOccurrences = new Map<string, number>();
    for (const node of incoming) {
      if (node.kind === "folder") {
        const title = node.title.trim() || "Folder";
        const occurrence = folderOccurrences.get(title) ?? 0;
        folderOccurrences.set(title, occurrence + 1);
        let folder = target.children.filter(
          (child): child is BookmarkFolder => child.kind === "folder" && child.title === title,
        )[occurrence];
        if (!folder) {
          folder = { kind: "folder", id: newId("f"), title, children: [] };
          const before = imported;
          merge(folder, node.children);
          if (imported > before) target.children.push(folder);
        } else {
          merge(folder, node.children);
        }
        continue;
      }

      const url = canonicalHttpUrl(node.url);
      if (!url) continue;
      const legacy = legacyByUrl.get(url);
      if (legacy) {
        const index = legacy.parent.children.findIndex((child) => child.id === legacy.node.id);
        if (index >= 0) legacy.parent.children.splice(index, 1);
        target.children.push(legacy.node);
        legacyByUrl.delete(url);
        imported += 1;
        changed = true;
        continue;
      }
      if (existingUrls.has(url)) continue;
      existingUrls.add(url);
      target.children.push({
        kind: "bookmark",
        id: newId("b"),
        title: node.title.trim() || url,
        url,
        ...(node.faviconUrl ? { faviconUrl: node.faviconUrl } : {}),
      });
      imported += 1;
      changed = true;
    }
  };

  merge(root, values);
  if (legacyFolder?.children.length === 0) {
    root.children = root.children.filter((child) => child.id !== legacyFolder.id);
    changed = true;
  }
  if (changed && !persistTree(root)) {
    throw new Error("bookmark storage unavailable");
  }
  return imported;
}
