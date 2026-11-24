"use client";

/** A tab that holds another application's window.
 *
 *  Without a window yet it lists the open ones to choose from. With one, on
 *  Windows and Linux the window itself sits in the tab as a child of the App
 *  window, and the tab only tells the worker where. On macOS the tab shows a
 *  live capture of the window, which the worker keeps right behind the tab,
 *  and sends the pointer, wheel and keys from the tab back to it; text typed
 *  through an input method arrives composed. Closing the tab puts the window
 *  back where it was. */

import { AppWindow, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  embedAvailable, embedHost, listWindows, windowIcons, type EmbedMode, type EmbeddableWindow,
} from "@/lib/desktop/embedded-window/embedded-window";
import { useTranslation } from "@/lib/i18n";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { LiveWindowView, type LiveWindowProblem } from "./live-window-view";

export function WindowTabPane({ tabId, windowId, app }: { tabId: string; windowId?: number; app?: string }) {
  const { text } = useTranslation();
  if (!embedAvailable()) {
    return <Notice icon text={text(
      "Other applications' windows can be shown only in the OpenProgram desktop App.",
      "只有在 OpenProgram 桌面 App 里才能显示其他应用的窗口。")} />;
  }
  if (!windowId) return <WindowPicker tabId={tabId} />;
  return <EmbeddedWindow key={windowId} tabId={tabId} windowId={windowId} app={app} />;
}

function Notice({ text, icon, action }: { text: string; icon?: boolean; action?: React.ReactNode }) {
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-3 p-6 text-center text-fs-sm text-muted-foreground">
      {icon && <AppWindow size={28} aria-hidden="true" />}
      <p className="max-w-[420px]">{text}</p>
      {action}
    </div>
  );
}

function WindowPicker({ tabId }: { tabId: string }) {
  const { text } = useTranslation();
  const setTarget = useCenterTabs((state) => state.setWindowTabTarget);
  const [windows, setWindows] = useState<EmbeddableWindow[] | null>(null);
  const [mode, setMode] = useState<EmbedMode>("native");
  // A list read that fails keeps what is shown and is asked again, quietly.
  const failures = useRef(0);
  const retry = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const refresh = useCallback(() => {
    clearTimeout(retry.current);
    embedHost().then(listWindows).then((result) => {
      failures.current = 0;
      for (const item of result.windows) if (item.icon) windowIcons.set(item.id, item.icon);
      setMode(result.mode);
      setWindows(result.windows);
    }).catch((reason: unknown) => {
      console.warn("window list read failed; reading again", reason);
      retry.current = setTimeout(() => refresh(), Math.min(30_000, 1000 * 2 ** failures.current++));
    });
  }, []);
  useEffect(() => () => clearTimeout(retry.current), []);
  useEffect(() => {
    refresh();
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [refresh]);

  return (
    <div className="flex h-full w-full justify-center overflow-auto bg-background">
      <div className="flex w-full max-w-[640px] flex-col gap-4 px-6 py-10">
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-1">
            <h2 className="text-fs-lg font-medium text-foreground">{text("Choose a window", "选择一个窗口")}</h2>
            <p className="text-fs-sm text-muted-foreground">{mode === "stream"
              ? text("The window moves behind this tab and you use it here, as if it were part of OpenProgram. Close the tab to put it back.",
                "窗口会移到这个标签页的后面，你在这里直接使用它，就像它是 OpenProgram 的一部分。关闭标签页，它就回到原来的位置。")
              : text("The window moves into this tab and works as part of OpenProgram. Close the tab to put it back.",
                "窗口会移进这个标签页，作为 OpenProgram 的一部分使用。关闭标签页，它就回到原来的位置。")}</p>
          </div>
          <Button variant="ghost" size="sm" onClick={refresh} aria-label={text("Refresh", "刷新")}>
            <RefreshCw aria-hidden="true" />
          </Button>
        </div>
        {windows && windows.length === 0 && (
          <p className="py-10 text-center text-fs-sm text-muted-foreground">
            {text("No other application has a window open on this desktop.", "这个桌面上没有其他应用打开的窗口。")}
          </p>
        )}
        {windows && windows.length > 0 && (
          <ul className="flex flex-col gap-1">
            {windows.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => setTarget(tabId, { id: item.id, app: item.app, title: item.title })}
                  className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left hover:bg-muted focus-visible:bg-muted focus-visible:outline-none"
                >
                  {item.icon
                    ? <img src={item.icon} width={28} height={28} alt="" aria-hidden="true" className="shrink-0" />
                    : <AppWindow size={28} aria-hidden="true" className="shrink-0 text-muted-foreground" />}
                  <span className="flex min-w-0 flex-col">
                    <span className="truncate text-fs-sm text-foreground">{item.app}</span>
                    <span className="truncate text-fs-sm text-muted-foreground">{item.title || text("Untitled window", "无标题窗口")}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function EmbeddedWindow({ tabId, windowId, app }: { tabId: string; windowId: number; app?: string }) {
  const { text } = useTranslation();
  const setTarget = useCenterTabs((state) => state.setWindowTabTarget);
  const setTitle = useCenterTabs((state) => state.setWindowTabTitle);
  const [problem, setProblem] = useState<LiveWindowProblem | null>(null);
  const onTitle = useCallback((title: string) => setTitle(tabId, title), [setTitle, tabId]);
  const onProblem = useCallback((next: LiveWindowProblem) => {
    // A restored tab whose window id now names another application's window.
    if (next.kind === "replaced") setTarget(tabId, null);
    else setProblem(next);
  }, [setTarget, tabId]);

  if (problem) {
    return <Notice icon text={problem.kind === "closed" ? text("This window was closed.", "这个窗口已经关闭了。")
      : problem.kind === "error" ? problem.message : ""} action={
      <Button variant="elevated" size="sm" onClick={() => setTarget(tabId, null)}>{text("Choose another window", "选择其他窗口")}</Button>
    } />;
  }
  return <LiveWindowView windowId={windowId} app={app} place onTitle={onTitle} onProblem={onProblem} />;
}
