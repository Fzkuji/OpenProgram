import { useCenterTabs } from '../center-tabs-store';
import { desktopBridge } from '../../desktop/desktop-bridge';
import { jsonFetch } from '../../net/fetch-client';
import { ingestBrowserResource, type BackendResource } from '../../chat/resources/session-resources';
import type { TabDragSubject } from './tab-drag-coordinator';
import { useSessionStore } from '../../session-store';
import { showToast } from '../../format-utils/toast';
import { translateText } from '../../i18n';

/** A split tab can carry its single webpage into Resources without attaching
 * the conversation itself. Multiple webpages require an individual pane drag. */
export function resourceDropTabId(subject: TabDragSubject): string | null {
  const pages = useCenterTabs.getState().tabs.filter(tab =>
    subject.tabIds.includes(tab.id) && tab.kind === 'web');
  return pages.length === 1 ? pages[0].id : null;
}

/** Pointer capture keeps event.target on the tab; hit-test the visible target instead. */
export function resourceDropTarget(subject: TabDragSubject, x: number, y: number): HTMLElement | null {
  if (!desktopBridge() || !resourceDropTabId(subject)) return null;
  // The rendered sidebar owns the scope, including the last focused chat in a
  // mixed canvas. Re-deriving it from activeId would select the dragged webpage.
  for (const element of document.querySelectorAll<HTMLElement>('[data-resource-drop-session]')) {
    if (!element.dataset.resourceDropSession) continue;
    const r = element.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return element;
  }
  return null;
}

export async function attachWebTabResource(tabId: string, sessionId: string): Promise<void> {
  const bridge = desktopBridge();
  const tab = useCenterTabs.getState().tabs.find(tab => tab.id === tabId && tab.kind === 'web');
  if (!bridge || !tab) throw new Error('page_unavailable');
  const result = await jsonFetch(`/api/session/${encodeURIComponent(sessionId)}/resources/attach-web`, {
    method: 'POST', body: JSON.stringify({ window_id: bridge.windowId, tab_id: tabId }),
  }) as { items: BackendResource[] };
  if (!Array.isArray(result.items) || !result.items.some(row => row.tab_id === tabId && row.conversation_session_id === sessionId)) {
    throw new Error('attachment_unconfirmed');
  }
  for (const row of result.items) ingestBrowserResource(row, sessionId);
}

/** Both drag entries wait for the native lock and report the actual result. */
export async function dropWebTabResource(tabId: string, sessionId: string, transferToken?: string): Promise<boolean> {
  try {
    if (transferToken && !await desktopBridge()?.tabTransfer.cancel(transferToken)) {
      throw new Error('transfer_unavailable');
    }
    await attachWebTabResource(tabId, sessionId);
    // Do not switch an unrelated conversation's panel if the user navigated
    // elsewhere while the native Page association was pending.
    if ([...document.querySelectorAll<HTMLElement>('[data-resource-drop-session]')]
      .some(el => el.dataset.resourceDropSession === sessionId)) {
      useSessionStore.getState().setRightDockView('resources');
      useSessionStore.getState().setRightDockOpen(true);
    }
    showToast(translateText('Webpage added to Resources', '网页已添加到资源'));
    return true;
  } catch {
    showToast(translateText('Could not attach webpage. Try again.', '网页添加失败，请重试。'), { tone: 'error' });
    return false;
  }
}
