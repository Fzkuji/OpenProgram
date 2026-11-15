"use client";
import { useEffect, useState, type CSSProperties } from "react";
import { FileTextIcon } from "@/components/animated-icons";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";
import { FileTabPane } from "./file-tab-pane";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import styles from "./persistent-file-panes.module.css";

type FilePaneTab = { id: string; kind: string; projectId?: string; path?: string; readOnly?: boolean; fileSessionId?: string; previewOwnerSessionId?: string };
function documentKey(tab: FilePaneTab): string | null {
  if (tab.kind !== "file" || !tab.path) return null;
  return tab.readOnly && tab.fileSessionId ? `attachment:${tab.fileSessionId}:${tab.path}`
    : tab.projectId ? `project:${tab.projectId}:${tab.path}` : null;
}

export function PersistentFilePanes({ tabs, activeFileIds, layouts }: { tabs: FilePaneTab[]; activeFileIds: Set<string>; layouts: Map<string, { className: string; style?: CSSProperties }> }) {
  const requests = useCenterTabs(state => state.fileSectionRequests);
  const consume = useCenterTabs(state => state.consumeFileSection);
  const [visited, setVisited] = useState<string[]>([]);
  const [preferred, setPreferred] = useState<Record<string, string>>({});
  const { text } = useTranslation();
  useEffect(() => {
    setVisited(current => {
      const alive = new Set(tabs.map(documentKey));
      const next = current.filter(key => alive.has(key));
      for (const tab of tabs) {
        const key = documentKey(tab);
        if (key && activeFileIds.has(tab.id) && !next.includes(key)) next.push(key);
      }
      return next.length === current.length && next.every((key, index) => current[index] === key) ? current : next;
    });
  }, [tabs, activeFileIds]);
  useEffect(() => {
    setPreferred(current => {
      const next = { ...current };
      for (const tab of tabs) {
        const key = documentKey(tab);
        if (key && activeFileIds.has(tab.id) && requests[tab.id]) next[key] = tab.id;
      }
      return next;
    });
  }, [requests, tabs, activeFileIds]);
  return <>{visited.map(key => {
    const aliases = tabs.filter(tab => documentKey(tab) === key);
    const active = aliases.filter(tab => activeFileIds.has(tab.id));
    const requested = active.filter(tab => requests[tab.id]).sort((a, b) => requests[b.id].sequence - requests[a.id].sequence)[0];
    const tab = requested ?? active.find(tab => tab.id === preferred[key]) ?? active[0] ?? aliases[0];
    if (!tab?.path) return null;
    const pane = (id: string, visible: boolean) => ({ ...layouts.get(id)?.style, display: visible ? layouts.get(id)?.style?.display : "none", minWidth: 0, minHeight: 0 });
    // One physical editor per document identity, retained when its tab alias
    // changes. A second native engine cannot acquire the same revision lease.
    return <div key={key} style={{ display: "contents" }}>
      <div data-file-pane-id={tab.id} data-page-surface="" className={layouts.get(tab.id)?.className} style={pane(tab.id, active.length > 0)}>
        <FileTabPane projectId={tab.projectId || ""} path={tab.path} sessionId={tab.fileSessionId} readOnly={tab.readOnly} previewSessionId={tab.previewOwnerSessionId} sectionNavigation={requests[tab.id] ? { request: requests[tab.id], consume: sequence => consume(tab.id, sequence) } : undefined} />
      </div>
      {active.filter(alias => alias.id !== tab.id).map(alias => <div key={alias.id} data-file-pane-id={alias.id} data-page-surface="" className={layouts.get(alias.id)?.className} style={pane(alias.id, true)}>
        <div className={styles.duplicate}><FileTextIcon size={24} aria-hidden /><span>{text("This file is open in another pane.", "此文件已在另一栏打开。")}</span>
          <Button type="button" variant="elevated" onClick={() => setPreferred(current => ({ ...current, [key]: alias.id }))}>{text("Show here", "在此显示")}</Button>
        </div>
      </div>)}
    </div>;
  })}</>;
}
