import type { useTranslation } from "@/lib/i18n";
import type { BuiltinPage } from "@/lib/state/center-tabs-store";

/** One name per built-in page, shared by the tab strip, the split
 *  picker and the main menu so the page is called the same thing
 *  wherever it appears. Its own module to keep the strip and the
 *  picker (which the strip imports) from importing each other. */
export function builtinPageLabel(
  page: BuiltinPage | undefined,
  text: ReturnType<typeof useTranslation>["text"],
): string {
  return page === "history"
    ? text("Web history", "网页历史")
    : text("Bookmarks", "书签");
}
