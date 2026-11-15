"use client";
import type { FileSectionNavigation } from "@/lib/tabs/panes/file-section-requests";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Markdown } from "@/lib/format-utils/markdown";
import { splitFrontmatter } from "@/lib/format-utils/obsidian-document";
import { absRawFileUrl, rawFileUrl } from "@/lib/files/files-shared";
import { markdownDestination, resolveMarkdownFile } from "@/lib/files/markdown-paths";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { useTranslation } from "@/lib/i18n";
import styles from "./markdown-preview.module.css";

/** A markdown file read as a document, laid out like Obsidian's reading
 *  view: the file name as an inline title, the frontmatter as a
 *  Properties table, then the body with Obsidian's syntax (callouts,
 *  ==highlights==, #tags, [[wikilinks]], ![[image embeds]]). Relative
 *  images load from the document's own folder through the same scoped
 *  file routes as the file itself, and a relative link or wikilink opens
 *  that file in its own tab. Raw HTML stays escaped: a repository file is
 *  untrusted. */
export function MarkdownPreview({ source, projectId, path, abs = false, sessionId, sectionNavigation }: {
  source: string; projectId: string; path: string; abs?: boolean; sessionId?: string; sectionNavigation?: FileSectionNavigation;
}) {
  const { text } = useTranslation();
  const { properties, body } = useMemo(() => splitFrontmatter(source), [source]);
  const title = (path.split(/[\\/]/).pop() ?? path).replace(/\.(md|markdown|mdx)$/i, "");
  const article = useRef<HTMLElement>(null);
  const [missing, setMissing] = useState(false);
  const navigate = useCallback((fragment: string) => {
    let key: string;
    try { key = decodeURIComponent(fragment.replace(/^#/, "")).normalize("NFC"); }
    catch { setMissing(true); return; }
    const heading = Array.from(article.current?.querySelectorAll<HTMLElement>("[data-section-key]") ?? [])
      .find(element => element.getAttribute("data-section-key") === key);
    setMissing(!heading);
    heading?.scrollIntoView({ block: "start" });
  }, []);
  const lastRequest = useRef<number | null>(null);
  useEffect(() => {
    if (!sectionNavigation || lastRequest.current === sectionNavigation.request.sequence) return;
    lastRequest.current = sectionNavigation.request.sequence;
    navigate(sectionNavigation.request.fragment);
    sectionNavigation.consume(sectionNavigation.request.sequence);
  }, [sectionNavigation, navigate, source]);
  const resolveImage = useCallback((src: string) => {
    const target = resolveMarkdownFile(path, src);
    if (!target) return null;
    return (abs ? absRawFileUrl(target.path, sessionId) : rawFileUrl(projectId, target.path)) + target.fragment;
  }, [abs, path, projectId, sessionId]);

  function onClick(event: React.MouseEvent) {
    const anchor = (event.target as HTMLElement).closest("a[href]");
    if (!anchor) return;
    const href = anchor.getAttribute("href") ?? "";
    const destination = markdownDestination(href);
    if (destination.kind === "rejected") { event.preventDefault(); return; }
    if (destination.kind === "fragment") { event.preventDefault(); navigate(href); return; }
    if (destination.kind !== "local") return;
    const target = resolveMarkdownFile(path, href);
    if (!target) { event.preventDefault(); return; }
    event.preventDefault();
    const tabs = useCenterTabs.getState();
    if (!abs) tabs.openFileTab(projectId, target.path, ...(target.fragment ? [{ section: target.fragment }] : []));
    else if (sessionId) tabs.openFilePreview(sessionId, { projectId, path: target.path, readOnly: true, ...(target.fragment ? { section: target.fragment } : {}) });
  }

  return <div className={styles.scroll} onClick={onClick}>
    <article ref={article} className={styles.document}>
      <h1 className={styles.inlineTitle}>{title}</h1>
      {properties.length > 0 && <section className={styles.properties} aria-label={text("Properties", "属性")}>
        <div className={styles.propertiesHeading}>{text("Properties", "属性")}</div>
        <dl>
          {properties.map(({ key, values }, index) => <div key={`${key}-${index}`} className={`${styles.property} grid grid-cols-[minmax(96px,_160px)_1fr] gap-[12px] p-[4px_0]`}>
            <dt>{key}</dt>
            <dd>{values.length > 1 || /^(tags|aliases|cssclasses)$/i.test(key)
              ? values.map((value, i) => <span key={i} className={styles.pill}>{value}</span>)
              : values[0] ?? <span className={styles.empty}>{text("Empty", "空")}</span>}</dd>
          </div>)}
        </dl>
      </section>}
      {missing && <p role="status" className={styles.missing}>{text("Section not found.", "未找到章节。")}</p>}
      <Markdown source={body} escapeRawHtml obsidian resolveImage={resolveImage} />
    </article>
  </div>;
}
