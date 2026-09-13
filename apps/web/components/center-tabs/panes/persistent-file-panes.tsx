"use client";
import { useEffect, useState } from "react";
import { FileTabPane } from "./file-tab-pane";

export function PersistentFilePanes({ tabs, activeFileIds, layouts }: { tabs: Array<{ id: string; kind: string; projectId?: string; path?: string }>; activeFileIds: Set<string>; layouts: Map<string, { className: string; style?: { order: number; width?: string } }> }) {
  const [visited, setVisited] = useState<string[]>([]);
  useEffect(() => {
    const activeFiles = tabs.filter((tab) => tab.kind === "file" && activeFileIds.has(tab.id) && tab.projectId && tab.path);
    setVisited((current) => {
      const next = current.filter((id) => tabs.some((tab) => tab.id === id));
      for (const tab of activeFiles) if (!next.includes(tab.id)) next.push(tab.id);
      return next.length === current.length && next.every((id, index) => current[index] === id) ? current : next;
    });
  }, [tabs, activeFileIds]);
  return <>{visited.map((id) => {
    const tab = tabs.find((candidate) => candidate.id === id);
    if (!tab?.projectId || !tab.path) return null;
    return <div key={id} data-file-pane-id={id} className={layouts.get(id)?.className} style={{ ...layouts.get(id)?.style, display: activeFileIds.has(id) ? undefined : "none", minWidth: 0, minHeight: 0 }}>
      <FileTabPane projectId={tab.projectId} path={tab.path} />
    </div>;
  })}</>;
}

