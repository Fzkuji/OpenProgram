/**
 * Composer — the chat input area, lifted out of `web/public/html/index.html`
 * into a React component.
 *
 * Migration status (slice 1 / N):
 *   - DOM structure is reproduced 1:1 from the legacy template, with the
 *     same element IDs and class names so existing CSS in
 *     `web/app/styles/05-chat.css` continues to apply and legacy JS in
 *     `web/public/js/chat/*.js` + `web/public/js/shared/*.js` can keep
 *     reaching DOM nodes via getElementById(...).
 *   - Event handlers still delegate to the legacy global functions
 *     (`onSendBtnClick`, `togglePlusMenu`, `toggleThinkingMenu`, ...).
 *   - State (input value, thinking effort, tools toggle, plus-menu open,
 *     slash-menu rendering) is therefore still owned by the legacy JS —
 *     we just rebuilt the chrome in React.
 *   - Subsequent slices will pull state and handlers into the React
 *     component progressively, and eventually delete the matching
 *     legacy JS.
 *
 * Caller contract: AppShell mounts <Composer /> once when the user is
 * on a chat route. PageShell strips the original `.input-area` block
 * out of the injected HTML so we don't end up with two input boxes.
 */
"use client";

import { useEffect, useRef } from "react";

// Lightweight typed handle into the legacy window globals so we can
// call into them without TypeScript complaining. Anything declared
// here exists in `web/public/js/chat/*.js`.
type LegacyWindow = Window & {
  onSendBtnClick?: () => void;
  stopExecution?: () => void;
  togglePlusMenu?: (e: Event) => void;
  toggleToolsEnabled?: (e: Event) => void;
  toggleWebSearchEnabled?: (e: Event) => void;
  renderPlusMenu?: () => void;
  toggleThinkingMenu?: (e: Event) => void;
};

function call(name: keyof LegacyWindow, e?: React.SyntheticEvent) {
  const w = window as LegacyWindow;
  const fn = w[name];
  if (typeof fn === "function") {
    // Cast intentional — legacy handlers accept the native event,
    // not the synthetic React one. Passing the synthetic event works
    // for ``.stopPropagation()`` (the only thing they call on it).
    (fn as (ev?: Event) => void)(e ? (e.nativeEvent as Event) : undefined);
  }
}

export function Composer() {
  const wrapperRef = useRef<HTMLDivElement>(null);

  // Once the React DOM is in place, let the legacy init code run any
  // setup that used to fire on DOMContentLoaded but missed our window
  // (PageShell stripped the input-area before legacy init ran). The
  // sentinel below is set when the composer mounts so legacy code can
  // detect "we've taken over" if it ever needs to.
  useEffect(() => {
    (window as LegacyWindow & { __composerMounted?: boolean }).__composerMounted = true;
    // Repaint plus-menu + thinking selector with whatever the legacy
    // globals are currently storing (was previously done once on
    // chat.js init; without re-running it here the React-rendered
    // buttons start blank).
    const w = window as LegacyWindow;
    try {
      w.renderPlusMenu?.();
    } catch {
      /* ignore — legacy init may not have finished yet */
    }
    return () => {
      (window as LegacyWindow & { __composerMounted?: boolean }).__composerMounted = false;
    };
  }, []);

  return (
    <div className="input-area" ref={wrapperRef}>
      {/* Clip container: only the area ABOVE the input-wrapper's top
          edge is visible. The slashMenu inside slides down past the
          clip's bottom edge to "vanish into" the input box. */}
      <div className="slash-clip">
        <div id="slashMenu" className="slash-menu" style={{ display: "none" }} />
      </div>
      <div className="input-wrapper">
        <button
          className="send-btn"
          id="sendBtn"
          onClick={(e) => call("onSendBtnClick", e)}
          title="Send message"
        >
          <svg viewBox="0 0 24 24">
            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
          </svg>
        </button>
        <button
          className="stop-btn"
          id="stopBtn"
          onClick={(e) => call("stopExecution", e)}
          title="Stop"
          style={{ display: "none" }}
        >
          <svg viewBox="0 0 24 24">
            <rect x="6" y="6" width="12" height="12" rx="2" />
          </svg>
        </button>
        <div className="input-top-row">
          <textarea
            id="chatInput"
            className="chat-input"
            placeholder="create / run / edit or ask anything... (type / for commands)"
            rows={1}
            defaultValue=""
          />
        </div>
        <div className="input-bottom-row">
          <div className="input-options">
            <button
              className="plus-btn"
              id="plusBtn"
              onClick={(e) => call("togglePlusMenu", e)}
              title="Add tools, files, and more"
              aria-label="More options"
            >
              <svg
                width="16"
                height="16"
                viewBox="0 0 20 20"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.75"
                strokeLinecap="round"
              >
                <line x1="10" y1="4" x2="10" y2="16" />
                <line x1="4" y1="10" x2="16" y2="10" />
              </svg>
            </button>
            <div className="active-tool-chips" id="activeToolChips" />
            <div
              className="plus-menu"
              id="plusMenu"
              onClick={(e) => e.stopPropagation()}
            >
              <div
                className="plus-menu-item"
                id="plusMenuTools"
                onClick={(e) => {
                  call("toggleToolsEnabled", e);
                  (window as LegacyWindow).renderPlusMenu?.();
                }}
                title="Shell, read/write/edit, grep/glob, list, patch, todo"
              >
                <div className="plus-menu-left">
                  <span className="plus-menu-icon">
                    <svg
                      width="20"
                      height="20"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.75"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
                    </svg>
                  </span>
                  <span className="plus-menu-label">Tools</span>
                </div>
                <div className="plus-menu-right" id="plusMenuToolsCheck" />
              </div>
              <div
                className="plus-menu-item"
                id="plusMenuWebSearch"
                onClick={(e) => {
                  call("toggleWebSearchEnabled", e);
                  (window as LegacyWindow).renderPlusMenu?.();
                }}
                title="Give the agent web search this turn"
              >
                <div className="plus-menu-left">
                  <span className="plus-menu-icon">
                    <svg
                      width="20"
                      height="20"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.75"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <circle cx="12" cy="12" r="10" />
                      <line x1="2" y1="12" x2="22" y2="12" />
                      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
                    </svg>
                  </span>
                  <span className="plus-menu-label">
                    Web Search
                    <span
                      className="plus-menu-sublabel"
                      id="plusMenuWebSearchSub"
                    />
                  </span>
                </div>
                <div className="plus-menu-right" id="plusMenuWebSearchCheck" />
              </div>
            </div>
            <div
              className="thinking-selector"
              id="thinkingSelector"
              onClick={(e) => call("toggleThinkingMenu", e)}
            >
              <span id="thinkingLabel">effort: …</span>
              <svg
                className="thinking-arrow"
                width="10"
                height="10"
                viewBox="0 0 10 10"
              >
                <path
                  d="M2 3.5L5 6.5L8 3.5"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  fill="none"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            <div className="thinking-menu" id="thinkingMenu" />
          </div>
          <span
            id="tokenBadge"
            className="context-stats-label"
            style={{ display: "none" }}
            title="Context token usage"
          />
        </div>
      </div>
    </div>
  );
}
