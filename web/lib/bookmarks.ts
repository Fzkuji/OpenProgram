export interface Bookmark {
  title: string;
  url: string;
}

export const BOOKMARKS_STORAGE_KEY = "openprogram.bookmarks";
export const BOOKMARKS_CHANGE_EVENT = "openprogram:bookmarks-changed";

export function readBookmarks(): Bookmark[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(BOOKMARKS_STORAGE_KEY) || "[]");
    return Array.isArray(parsed)
      ? parsed.filter(
          (bookmark): bookmark is Bookmark =>
            typeof bookmark?.title === "string" && typeof bookmark?.url === "string",
        )
      : [];
  } catch {
    return [];
  }
}

function saveBookmarks(bookmarks: Bookmark[]): Bookmark[] {
  localStorage.setItem(BOOKMARKS_STORAGE_KEY, JSON.stringify(bookmarks));
  window.dispatchEvent(new Event(BOOKMARKS_CHANGE_EVENT));
  return bookmarks;
}

export function toggleBookmark(bookmark: Bookmark): Bookmark[] {
  const bookmarks = readBookmarks();
  const index = bookmarks.findIndex((item) => item.url === bookmark.url);
  return saveBookmarks(index === -1 ? [...bookmarks, bookmark] : bookmarks.filter((_, i) => i !== index));
}

export function removeBookmark(url: string): Bookmark[] {
  return saveBookmarks(readBookmarks().filter((bookmark) => bookmark.url !== url));
}

export function isBookmarked(url: string): boolean {
  return readBookmarks().some((bookmark) => bookmark.url === url);
}
