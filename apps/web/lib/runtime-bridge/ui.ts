/**
 * UI state — run flag, status badge, thinking menu, plus menu.
 *
 * Consumers import these exports directly. Many functions write to
 * legacy composer / topbar dom ids; where the React component owns
 * that chip the element is absent and the write is a guarded no-op.
 * Imported for side effects by AppShell.
 */

import { useSessionStore, type StatusTone } from "@/lib/session-store";
import { pushStatusBadge, setLastStatus } from "@/lib/top-bar-sync";
import { startChannelHealthPoll, stopChannelHealthPoll } from "./conversations";
import { escAttr } from "./helpers";
import { runtimeState } from "./state";

/** The ONLY reason anything below still lives on `window`: this file
 *  generates HTML strings containing inline `onclick="…"` handlers,
 *  which resolve against `window` at click time. Every TS consumer
 *  imports the exports directly. Two generated-markup sites remain:
 *  `buildThinkingMenu` (→ `setThinkingEffort`) and
 *  `renderActiveToolChips` (→ `toggleToolsEnabled` /
 *  `toggleWebSearchEnabled` / `_updatePlusBtnIndicator`). */
interface UiWindow {
  setThinkingEffort?: typeof setThinkingEffort;
  _updatePlusBtnIndicator?: typeof updatePlusBtnIndicator;
  toggleToolsEnabled?: typeof toggleToolsEnabled;
  toggleWebSearchEnabled?: typeof toggleWebSearchEnabled;
}

const W = (
  typeof window === "undefined" ? {} : window
) as unknown as UiWindow;

/* ===== Run / pause =============================================== */

export function setRunning(running: boolean): void {
  runtimeState.isRunning = running;
  if (!running) runtimeState.isPaused = false;

  // The React composer's send/stop button reads its own session scope's
  // ``running``, not this legacy flag. Bridge them via the keyed setter so
  // ``running_task`` WS events (which land in legacy handlers and call
  // ``setRunning(true)``) actually flip the button.
  const sid = runtimeState.currentSessionId || "";
  if (sid) {
    const store = useSessionStore.getState();
    if (running) {
      // 已有带 msg_id/execution_id 的真实 task 时不要用空占位覆盖——
      // 冲掉 execution_id 会让停止键发不出 execution.cancel。
      const existing = store.runningTasks[sid];
      if (!existing || (!existing.msg_id && !existing.execution_id)) {
        store.setRunningTaskFor(sid, { session_id: sid, msg_id: "" });
      }
    } else {
      store.setRunningTaskFor(sid, null);
    }
  }

  updateSendBtn();
  const chatInput = document.getElementById("chatInput") as HTMLTextAreaElement | null;
  if (chatInput) {
    chatInput.placeholder = running
      ? "Waiting for response..."
      : "create / run / fix or ask anything...";
  }
  document.querySelectorAll<HTMLButtonElement>(".fn-form-run-btn").forEach((b) => {
    b.disabled = running;
    b.style.opacity = running ? "0.4" : "";
    b.style.cursor = running ? "not-allowed" : "";
  });
}

// Real stats come from the server via the context_stats handler.
export function updateContextStats(): void {}

const SVG_SEND =
  '<svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>';
const SVG_PAUSE =
  '<svg viewBox="0 0 24 24"><rect x="5" y="4" width="4" height="16" rx="1"/><rect x="15" y="4" width="4" height="16" rx="1"/></svg>';
const SVG_RESUME = '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';

function renderStatusBadge(
  text: string,
  klass: string,
  dotKlass: string,
  // Hover text for the chip's HoverTip. Defaults to `text` (the short
  // label) but callers pass the fuller "connected · <source>" string.
  hoverTitle?: string,
): void {
  // #statusBadge is owned by the React <TopBar> (StatusBadge → Laptop
  // icon). Writing innerHTML here clobbered React's render and made the
  // icon flicker back to the legacy dot. Push to the session store and
  // let React render the chip instead — unconditionally, since the DOM
  // element may not exist at all.
  const tone: StatusTone = dotKlass.includes("--ok")
    ? "ok"
    : dotKlass.includes("--warn")
      ? "warn"
      : dotKlass.includes("--err")
        ? "err"
        : "connecting";
  pushStatusBadge({
    label: text.split(" · ")[0],
    tone,
    paused: klass.includes("paused"),
    title: hoverTitle ?? text,
  });
}

export function setStatusDotHealth(state: string): void {
  const mod = state === "ok" ? "--ok" : state === "warn" ? "--warn" : state === "err" ? "--err" : "--neutral";
  const dot = document.getElementById("statusBadge")?.querySelector(".indicator-dot");
  if (dot) dot.className = "indicator-dot sm " + mod;
  const tone: StatusTone =
    state === "ok" ? "ok" : state === "warn" ? "warn" : state === "err" ? "err" : "connecting";
  try {
    const store = useSessionStore.getState();
    store.setStatusBadge({ ...store.statusBadge, tone });
  } catch {
    /* ignore */
  }
}

export function updateSendBtn(): void {
  const sendBtn = document.getElementById("sendBtn");
  const stopBtn = document.getElementById("stopBtn");

  if (sendBtn && stopBtn) {
    if (!runtimeState.isRunning) {
      sendBtn.innerHTML = SVG_SEND;
      sendBtn.title = "Send message";
      sendBtn.className = "send-btn";
      stopBtn.style.display = "none";
    } else if (runtimeState.isPaused) {
      sendBtn.innerHTML = SVG_RESUME;
      sendBtn.title = "Resume";
      sendBtn.className = "send-btn paused-state";
      stopBtn.style.display = "flex";
    } else {
      sendBtn.innerHTML = SVG_PAUSE;
      sendBtn.title = "Pause";
      sendBtn.className = "send-btn";
      stopBtn.style.display = "none";
    }
  }

  // `deriveStatusBadge` already maps isPaused / isRunning → paused /
  // running, so the plain push covers all three branches above.
  pushStatusBadge();
}

export function updatePauseBtn(): void {
  updateSendBtn();
}

export function updateStatus(status: string, source?: string): void {
  setLastStatus(status, source);
  const connected = status === "connected";
  const dotKlass = connected ? "indicator-dot sm --ok" : "indicator-dot sm --err";
  const text = connected ? source || "Local" : "disconnected";
  const hoverTitle = connected
    ? source
      ? "connected · " + source
      : "connected · local worker"
    : "disconnected";
  renderStatusBadge(
    text,
    connected ? "status-badge" : "status-badge disconnected",
    dotKlass,
    hoverTitle,
  );
  // No native `title` here: it produced the OS's black tooltip on top of
  // our own HoverTip (Radix Tooltip), so the chip showed two tooltips at
  // once. The chip's hover text is supplied to the store as
  // statusBadge.title and rendered only by HoverTip.
}

/* ===== Channel / title helpers =================================== */

const CHANNEL_BRAND: Record<string, string> = {
  wechat: "WeChat",
  discord: "Discord",
  telegram: "Telegram",
  slack: "Slack",
};

function channelBrand(channel: string): string {
  if (!channel) return "";
  return CHANNEL_BRAND[String(channel).toLowerCase()] || channel;
}

function channelPrefixFor(channel: string, accountId?: string): string {
  if (!channel) return "";
  const brand = channelBrand(channel);
  return accountId ? brand + " (" + accountId + ")" : brand;
}

export function refreshStatusSource(): void {
  const cid = runtimeState.currentSessionId ?? null;
  const conv = (cid ? runtimeState.conversations[cid] : null) as
    | { channel?: string; account_id?: string; source?: string }
    | null
    | undefined;

  let ch: string | null = null;
  let acct: string | null = null;
  const pending = runtimeState._pendingChannelChoice;
  if (conv && conv.channel) {
    ch = conv.channel;
    acct = conv.account_id || null;
  } else if (pending && pending.channel) {
    ch = pending.channel;
    acct = pending.account_id || null;
  }

  const parts: string[] = [];
  if (ch) {
    parts.push(channelPrefixFor(ch, acct || undefined));
  } else if (conv && conv.source) {
    parts.push(conv.source);
  }
  updateStatus("connected", parts.join(" · "));

  if (ch) {
    startChannelHealthPoll(ch, acct || "default");
  } else {
    stopChannelHealthPoll();
  }
}

/* ===== Thinking menu (legacy DOM — mostly no-op now) ============= */

export function buildThinkingMenu(): void {
  const cfg = runtimeState._thinkingConfig;
  if (!cfg) return;
  const menu = document.getElementById("thinkingMenu");
  const label = document.getElementById("thinkingLabel");
  const selector = document.getElementById("thinkingSelector");
  if (!menu || !label) return;

  const options = (cfg.options || []).slice();
  if (!options.length) {
    if (selector) selector.style.display = "none";
    menu.classList.remove("open");
    runtimeState._thinkingEffort = null;
    return;
  }
  if (selector) selector.style.display = "";

  let currentEffort = runtimeState._thinkingEffort;
  const values = options.map((o) => o.value);
  if (!currentEffort || values.indexOf(currentEffort) < 0) {
    currentEffort = cfg.default || values[0];
    runtimeState._thinkingEffort = currentEffort;
  }
  label.textContent = "effort: " + currentEffort;

  menu.innerHTML = options
    .map((o) => {
      const sel = o.value === currentEffort;
      return (
        '<div class="thinking-option' +
        (sel ? " selected" : "") +
        "\" onclick=\"setThinkingEffort('" +
        o.value +
        "')\">" +
        '<span class="thinking-opt-label">' +
        o.value +
        "</span>" +
        '<span class="thinking-opt-desc">' +
        o.desc +
        "</span>" +
        '<span class="thinking-opt-check">' +
        (sel ? "&#10003;" : "") +
        "</span>" +
        "</div>"
      );
    })
    .join("");
}

export function closeAllPopovers(except?: string): void {
  if (except !== "thinking") {
    document.getElementById("thinkingMenu")?.classList.remove("open");
    document.getElementById("thinkingSelector")?.classList.remove("open");
  }
  if (except !== "plus") {
    document.getElementById("plusMenu")?.classList.remove("open");
    document.getElementById("plusBtn")?.classList.remove("open");
  }
  if (except !== "model") document.getElementById("modelDropdown")?.remove();
  if (except !== "user") {
    document.getElementById("userMenu")?.classList.remove("open");
  }
  if (except !== "agent") document.getElementById("agentSelector")?.remove();
  if (except !== "channel") document.getElementById("channelDropdown")?.remove();
  if (except !== "branch") document.getElementById("branchDropdown")?.remove();
}

export function setThinkingEffort(level: string): void {
  runtimeState._thinkingEffort = level;
  buildThinkingMenu();
  document.getElementById("thinkingMenu")?.classList.remove("open");
  document.getElementById("thinkingSelector")?.classList.remove("open");
}

/* ===== Plus menu ================================================= */

const PLUS_CHECK_SVG =
  '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">' +
  '<path d="M15.188 5.11a.5.5 0 0 1 .752.626l-.056.084-7.5 9a.5.5 0 0 1-.738.033l-3.5-3.5-.064-.078a.501.501 0 0 1 .693-.693l.078.064 3.113 3.113 7.15-8.58z"/>' +
  "</svg>";

export function renderPlusMenu(): void {
  const toolsItem = document.getElementById("plusMenuTools");
  const check = document.getElementById("plusMenuToolsCheck");
  if (toolsItem) toolsItem.classList.toggle("active", !!runtimeState._toolsEnabled);
  if (check) check.innerHTML = runtimeState._toolsEnabled ? PLUS_CHECK_SVG : "";

  const wsItem = document.getElementById("plusMenuWebSearch");
  const wsCheck = document.getElementById("plusMenuWebSearchCheck");
  const wsSub = document.getElementById("plusMenuWebSearchSub");
  if (wsItem) wsItem.classList.toggle("active", !!runtimeState._webSearchEnabled);
  if (wsCheck) {
    wsCheck.innerHTML = runtimeState._webSearchEnabled ? PLUS_CHECK_SVG : "";
  }
  if (wsSub) {
    const provName = runtimeState._webSearchProviderLabel || "";
    wsSub.textContent = provName ? " · " + provName : "";
  }
  updatePlusBtnIndicator();
}

export function updatePlusBtnIndicator(): void {
  const btn = document.getElementById("plusBtn");
  const anyActive =
    !!runtimeState._toolsEnabled || !!runtimeState._webSearchEnabled;
  if (btn) btn.classList.toggle("has-active", anyActive);
  renderActiveToolChips();
}

function renderActiveToolChips(): void {
  const host = document.getElementById("activeToolChips");
  if (!host) return;
  let chips = "";
  if (runtimeState._toolsEnabled) {
    chips +=
      '<div class="tool-chip" data-tooltip="Tools" onclick="toggleToolsEnabled(event); _updatePlusBtnIndicator();" title="">' +
      '<span class="tool-chip-icon">' +
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>' +
      "</span>" +
      '<span class="tool-chip-close" aria-label="Remove">' +
      '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><line x1="3" y1="3" x2="9" y2="9"/><line x1="9" y1="3" x2="3" y2="9"/></svg>' +
      "</span>" +
      "</div>";
  }
  if (runtimeState._webSearchEnabled) {
    let label = runtimeState._webSearchProviderLabel
      ? "Web Search · " + runtimeState._webSearchProviderLabel
      : "Web Search";
    if (runtimeState._webSearchProviderTier) {
      label += " · " + runtimeState._webSearchProviderTier;
    }
    chips +=
      '<div class="tool-chip" data-tooltip="' +
      escAttr(label) +
      '" onclick="toggleWebSearchEnabled(event); _updatePlusBtnIndicator();" title="">' +
      '<span class="tool-chip-icon">' +
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>' +
      "</span>" +
      '<span class="tool-chip-close" aria-label="Remove">' +
      '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><line x1="3" y1="3" x2="9" y2="9"/><line x1="9" y1="3" x2="3" y2="9"/></svg>' +
      "</span>" +
      "</div>";
  }
  host.innerHTML = chips;
}

export function toggleToolsEnabled(e?: Event): void {
  if (e) e.stopPropagation();
  runtimeState._toolsEnabled = !runtimeState._toolsEnabled;
  try {
    localStorage.setItem(
      "agentic_tools_enabled",
      runtimeState._toolsEnabled ? "1" : "0",
    );
  } catch {
    /* ignore */
  }
  updatePlusBtnIndicator();
}

export function toggleWebSearchEnabled(e?: Event): void {
  if (e) e.stopPropagation();
  runtimeState._webSearchEnabled = !runtimeState._webSearchEnabled;
  try {
    localStorage.setItem(
      "agentic_web_search_enabled",
      runtimeState._webSearchEnabled ? "1" : "0",
    );
  } catch {
    /* ignore */
  }
  if (runtimeState._webSearchEnabled && !runtimeState._webSearchProviderLabel) {
    refreshWebSearchProviderLabel();
  }
  updatePlusBtnIndicator();
}

export function refreshWebSearchProviderLabel(): void {
  try {
    fetch("/api/search-providers/list")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        const def = d.default;
        const prov =
          (d.providers || []).find((p: { id: string; available: boolean; is_default?: boolean }) =>
            def ? p.id === def : p.available && p.is_default !== false,
          ) ||
          (d.providers || []).find((p: { available: boolean }) => p.available);
        runtimeState._webSearchProviderLabel = prov ? prov.name : "";
        runtimeState._webSearchProviderTier = prov && prov.tier ? prov.tier : "";
        renderPlusMenu();
      })
      .catch(() => {});
  } catch {
    /* ignore */
  }
}

/* ===== Unified click-outside + plus-menu init ==================== */

/** Close every open popover when a click lands outside it. Installed at
 *  module load, so it has to tolerate a DOM-less host — this module is
 *  reachable from SSR and from the Node check scripts. */
function installClickOutside(): void {
  document.addEventListener("click", (e) => {
  const t = e.target as HTMLElement | null;
  if (!t) return;
  if (!t.closest("#plusMenu") && !t.closest("#plusBtn")) {
    document.getElementById("plusMenu")?.classList.remove("open");
    document.getElementById("plusBtn")?.classList.remove("open");
  }
  if (!t.closest("#thinkingMenu") && !t.closest("#thinkingSelector")) {
    document.getElementById("thinkingMenu")?.classList.remove("open");
    document.getElementById("thinkingSelector")?.classList.remove("open");
  }
  if (!t.closest("#modelDropdown") && !t.closest("#modelBadge")) {
    document.getElementById("modelDropdown")?.remove();
  }
  if (!t.closest("#userMenu") && !t.closest(".sidebar-footer")) {
    document.getElementById("userMenu")?.classList.remove("open");
  }
  if (
    !t.closest("#agentSelector") &&
    !t.closest("#chatAgentBadge") &&
    !t.closest("#execAgentBadge")
  ) {
    document.getElementById("agentSelector")?.remove();
    }
  });
}

if (typeof document !== "undefined") {
  installClickOutside();
  try {
    runtimeState._toolsEnabled =
      localStorage.getItem("agentic_tools_enabled") === "1";
  } catch {
    runtimeState._toolsEnabled = false;
  }
  setTimeout(() => updatePlusBtnIndicator(), 0);
}

/* ===== window bridges ============================================ */
/* Only the names the generated inline `onclick="…"` markup above
   resolves at click time. Everything else is a plain export. */

W.setThinkingEffort = setThinkingEffort;
W._updatePlusBtnIndicator = updatePlusBtnIndicator;
W.toggleToolsEnabled = toggleToolsEnabled;
W.toggleWebSearchEnabled = toggleWebSearchEnabled;
