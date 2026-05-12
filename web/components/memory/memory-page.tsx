"use client";

import { useState, useEffect } from "react";
import styles from "./memory-page.module.css";

interface WikiPage {
  path: string;
  title: string;
  type: string;
  size: number;
  mtime: number;
}

interface ShortTermEntry {
  date: string;
  size: number;
  mtime: number;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function formatDate(mtime: number): string {
  return new Date(mtime * 1000).toLocaleDateString();
}

export function MemoryPage() {
  const [tab, setTab] = useState<"wiki" | "short-term">("wiki");

  // Wiki state
  const [wikiPages, setWikiPages] = useState<WikiPage[]>([]);
  const [wikiLoading, setWikiLoading] = useState(true);
  const [selectedWiki, setSelectedWiki] = useState<WikiPage | null>(null);
  const [, setWikiContent] = useState("");
  const [editContent, setEditContent] = useState("");
  const [wikiPanelOpen, setWikiPanelOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"" | "saved" | "error">("");

  // Short-term state
  const [shortTermEntries, setShortTermEntries] = useState<ShortTermEntry[]>([]);
  const [shortTermLoading, setShortTermLoading] = useState(true);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [shortTermContent, setShortTermContent] = useState("");

  useEffect(() => {
    fetch("/api/memory/wiki")
      .then((r) => r.json())
      .then((data) => {
        setWikiPages(Array.isArray(data) ? data : []);
        setWikiLoading(false);
      })
      .catch(() => setWikiLoading(false));
  }, []);

  useEffect(() => {
    fetch("/api/memory/short-term")
      .then((r) => r.json())
      .then((data) => {
        setShortTermEntries(Array.isArray(data) ? data : []);
        setShortTermLoading(false);
      })
      .catch(() => setShortTermLoading(false));
  }, []);

  function openWikiPage(page: WikiPage) {
    setSelectedWiki(page);
    setWikiContent("");
    setEditContent("");
    setWikiPanelOpen(true);
    setSaveStatus("");
    fetch(`/api/memory/wiki/${page.path}`)
      .then((r) => r.json())
      .then((data) => {
        setWikiContent(data.content ?? "");
        setEditContent(data.content ?? "");
      });
  }

  function closeWikiPanel() {
    setWikiPanelOpen(false);
    setSelectedWiki(null);
  }

  async function saveWikiPage() {
    if (!selectedWiki) return;
    setSaving(true);
    setSaveStatus("");
    try {
      const r = await fetch(`/api/memory/wiki/${selectedWiki.path}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: editContent }),
      });
      if (r.ok) {
        setSaveStatus("saved");
        setWikiContent(editContent);
      } else {
        setSaveStatus("error");
      }
    } catch {
      setSaveStatus("error");
    } finally {
      setSaving(false);
    }
  }

  function openShortTerm(date: string) {
    setSelectedDate(date);
    setShortTermContent("");
    fetch(`/api/memory/short-term/${date}`)
      .then((r) => r.json())
      .then((data) => setShortTermContent(data.content ?? ""));
  }

  return (
    <div className={styles.view}>
      <div className={styles.topbar}>
        <span className={styles.title}>Memory</span>
        <div className={styles.tabs}>
          <button
            className={`${styles.tab} ${tab === "wiki" ? styles.tabActive : ""}`}
            onClick={() => setTab("wiki")}
          >
            Wiki
          </button>
          <button
            className={`${styles.tab} ${tab === "short-term" ? styles.tabActive : ""}`}
            onClick={() => setTab("short-term")}
          >
            Short-term
          </button>
        </div>
      </div>

      <div className={styles.body}>
        {tab === "wiki" && (
          <div className={styles.wikiLayout}>
            <div className={styles.list}>
              {wikiLoading ? (
                <div className={styles.empty}>Loading…</div>
              ) : wikiPages.length === 0 ? (
                <div className={styles.empty}>No wiki pages found.</div>
              ) : (
                wikiPages.map((page) => (
                  <div
                    key={page.path}
                    className={`${styles.listItem} ${selectedWiki?.path === page.path ? styles.listItemActive : ""}`}
                    onClick={() => openWikiPage(page)}
                  >
                    <div className={styles.listItemMain}>
                      <span className={styles.listItemTitle}>{page.title || page.path}</span>
                      {page.type && (
                        <span className={styles.badge}>{page.type}</span>
                      )}
                    </div>
                    <div className={styles.listItemMeta}>
                      <span>{page.path}</span>
                      <span>{formatSize(page.size)}</span>
                      <span>{formatDate(page.mtime)}</span>
                    </div>
                  </div>
                ))
              )}
            </div>

            {wikiPanelOpen && selectedWiki && (
              <div className={styles.panel}>
                <div className={styles.panelHeader}>
                  <span className={styles.panelTitle}>{selectedWiki.title || selectedWiki.path}</span>
                  <div className={styles.panelActions}>
                    {saveStatus === "saved" && (
                      <span className={styles.saveOk}>Saved</span>
                    )}
                    {saveStatus === "error" && (
                      <span className={styles.saveErr}>Error</span>
                    )}
                    <button
                      className={styles.saveBtn}
                      onClick={saveWikiPage}
                      disabled={saving}
                    >
                      {saving ? "Saving…" : "Save"}
                    </button>
                    <button className={styles.closeBtn} onClick={closeWikiPanel}>
                      ✕
                    </button>
                  </div>
                </div>
                <textarea
                  className={styles.editor}
                  value={editContent}
                  onChange={(e) => {
                    setEditContent(e.target.value);
                    setSaveStatus("");
                  }}
                  spellCheck={false}
                />
              </div>
            )}

            {!wikiPanelOpen && (
              <div className={styles.placeholder}>
                Select a page to view or edit
              </div>
            )}
          </div>
        )}

        {tab === "short-term" && (
          <div className={styles.wikiLayout}>
            <div className={styles.list}>
              {shortTermLoading ? (
                <div className={styles.empty}>Loading…</div>
              ) : shortTermEntries.length === 0 ? (
                <div className={styles.empty}>No short-term memory files found.</div>
              ) : (
                shortTermEntries.map((entry) => (
                  <div
                    key={entry.date}
                    className={`${styles.listItem} ${selectedDate === entry.date ? styles.listItemActive : ""}`}
                    onClick={() => openShortTerm(entry.date)}
                  >
                    <div className={styles.listItemMain}>
                      <span className={styles.listItemTitle}>{entry.date}</span>
                    </div>
                    <div className={styles.listItemMeta}>
                      <span>{formatSize(entry.size)}</span>
                      <span>{formatDate(entry.mtime)}</span>
                    </div>
                  </div>
                ))
              )}
            </div>

            {selectedDate ? (
              <div className={styles.panel}>
                <div className={styles.panelHeader}>
                  <span className={styles.panelTitle}>{selectedDate}</span>
                </div>
                <pre className={styles.viewer}>{shortTermContent || "Loading…"}</pre>
              </div>
            ) : (
              <div className={styles.placeholder}>
                Select a date to view its memory
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
