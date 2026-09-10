"use client";
import { useEffect, useRef, useState } from "react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { renderMarkdown } from "./markdown";
import { useTranslation } from "@/lib/i18n";
import styles from "./memory-page.module.css";

/** All Memory views use the same availability check before showing source text. */
export function MemorySourcePreview({ children }: { children: React.ReactNode }) {
  const { text } = useTranslation();
  const [source, setSource] = useState<{ content?: string; error?: string; session_id?: string } | null>(null);
  const generation = useRef(0);
  useEffect(() => () => { generation.current++; }, []);
  async function onClick(event: React.MouseEvent) {
    const anchor = (event.target as HTMLElement).closest("a[href]") as HTMLAnchorElement | null;
    if (!anchor) return;
    const target = new URL(anchor.href, window.location.href);
    if (target.origin !== window.location.origin || !target.pathname.startsWith("/sources/")) return;
    event.preventDefault();
    const request = ++generation.current;
    setSource({});
    try {
      const response = await fetch("/api/memory/source?" + new URLSearchParams({ path: decodeURIComponent(target.pathname.slice(9)) }));
      const data = await response.json();
      if (request !== generation.current) return;
      if (data.deleted) {
        anchor.textContent = text("Original session deleted", "原会话已删除");
        setSource({ error: text("Original session deleted. The memory remains available.", "原会话已删除，记忆本身仍保留。") });
      } else if (!response.ok) setSource({ error: data.error || text("Source unavailable", "来源不可用") });
      else setSource(data);
    } catch (error) { if (request === generation.current) setSource({ error: String(error) }); }
  }
  return <div style={{ display: "contents" }} onClick={onClick}>
    {children}
    <Dialog open={source !== null} onOpenChange={open => { if (!open) { generation.current++; setSource(null); } }}>
      <DialogContent className={styles.sourceDialog}>
        <DialogTitle>{text("Memory source", "记忆来源")}</DialogTitle>
        {source?.error ? <p role="alert">{source.error}</p> : source?.content !== undefined ?
          <><div className={styles.markdown} dangerouslySetInnerHTML={{ __html: renderMarkdown(source.content) }} />
            {source.session_id && <a href={"/s/" + encodeURIComponent(source.session_id)}>{text("Open original session", "打开原会话")}</a>}</> :
          <p>{text("Loading…", "加载中…")}</p>}
      </DialogContent>
    </Dialog>
  </div>;
}
