"use client";
import { useSessionStore } from '@/lib/session-store';
import { useSessionHistory } from '@/lib/chat/history/session-history';
import { retrySessionHistory, loadSessionHistoryWindow } from '@/lib/runtime-bridge/session/session-history-loader';
import { useTranslation } from '@/lib/i18n';
import { requestSessionLoad } from '@/lib/runtime-bridge/session/session-load';

export function TranscriptReadStatus({ sessionId }: { sessionId: string | null }) {
  const status = useSessionStore(s => sessionId ? s.transcriptReadStatus[sessionId] : undefined);
  const pageError = useSessionHistory(s => sessionId ? s.pages[sessionId]?.error : false);
  const { text } = useTranslation();
  if (sessionId && pageError && status !== 'error' && status !== 'disconnected') return <div role="status" className="flex flex-wrap items-center gap-3 px-4 py-3 text-fs-xs">
    <span>{text('Could not load more history.', '无法继续加载历史。')}</span>
    <button type="button" className="underline" onClick={() => void retrySessionHistory(sessionId)}>{text('Retry history', '重试历史加载')}</button>
    <button type="button" className="underline" onClick={() => void loadSessionHistoryWindow(sessionId, 'latest')}>{text('Go to latest history', '查看最新历史')}</button>
  </div>;
  if (!sessionId || (status !== 'error' && status !== 'disconnected')) return null;
  return <div role="status" className="flex items-center gap-3 px-4 py-3 text-fs-xs">
    <span>{status === 'disconnected' ? text('Disconnected. History is unavailable.', '连接已断开，暂时无法加载历史。') : text('Could not load history.', '历史加载失败。')}</span>
    <button type="button" className="underline" onClick={() => requestSessionLoad({ action: 'load_session', session_id: sessionId })}>{text('Retry', '重试')}</button>
  </div>;
}
