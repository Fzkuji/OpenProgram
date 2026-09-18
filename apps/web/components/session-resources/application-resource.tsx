"use client";
import { ApplicationTabPane } from "@/components/center-tabs/application-tab-pane";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import type { SessionResource } from "@/lib/chat/session-resources";
import { useTranslation } from "@/lib/i18n";

export function ApplicationResourceView({ resource, onHide }: { resource: SessionResource; onHide: () => void }) {
  const { text } = useTranslation();
  if (!resource.applicationInstanceId || !resource.applicationId) return null;
  return <section aria-label={text("Application resource", "应用资源")} className="flex min-h-80 flex-1 flex-col">
    <header className="flex gap-2 p-2"><strong className="flex-1">{resource.title}</strong>
      <button onClick={() => useCenterTabs.getState().openApplicationTab(resource.applicationId!, resource.applicationInstanceId!, resource.title)}>{text("Open in tab", "在标签页打开")}</button>
      <button onClick={onHide}>{text("Hide view", "隐藏视图")}</button>
    </header>
    <div className="min-h-80 flex-1"><ApplicationTabPane instanceId={resource.applicationInstanceId} /></div>
  </section>;
}
