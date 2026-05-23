/**
 * UI state — run/pause, status badge, thinking menu, plus menu,
 * detail panel, code viewer.
 *
 * TS port of `public/js/shared/ui.js`. Bridged onto `window.*` for the
 * still-legacy history-graph.js, inline-onclick HTML and React topbar.
 * Many functions write to legacy composer / topbar dom ids; where the
 * React component owns that chip the element is absent and the write
 * is a guarded no-op. Imported for side effects by AppShell.
 */

interface UiWindow {
  isRunning?: boolean;
  isPaused?: boolean;
  currentSessionId?: string | null;
  conversations?: Record<string, { channel?: string; account_id?: string; source?: string }>;
  selectedPath?: string | null;
  _thinkingConfig?: { options?: { value: string; desc: string }[]; default?: string };
  _thinkingEffort?: string | null;
  _toolsEnabled?: boolean;
  _webSearchEnabled?: boolean;
  _webSearchProviderLabel?: string;
  _webSearchProviderTier?: string;
  _pendingChannelChoice?: { channel?: string; account_id?: string } | null;
  rightDock?: { show: (tab: string) => void };
  escHtml?: (s: unknown) => string;
  escAttr?: (s: unknown) => string;
  highlightPython?: (code: string) => string;
  addSystemMessage?: (text: string) => void;
  sendMessage?: () => void;
  setInput?: (text: string) => void;
  _startChannelHealthPoll?: (ch: string, acct: string) => void;
  _stopChannelHealthPoll?: () => void;
  _updatePlusBtnIndicator?: () => void;
  [k: string]: unknown;
}

const W = window as unknown as UiWindow;

/* ===== Run / pause =============================================== */

export function setRunning(running: boolean): void {
  W.isRunning = running;
  if (!running) W.isPaused = false;

  // The React composer's send/stop button reads ``runningTask !== null``
  // from the session-store, not this legacy flag. Bridge them so
  // ``running_task`` WS events (which land in legacy handlers and
  // call ``setRunning(true)``) actually flip the button.
  const w = W as unknown as {
    __sessionStore?: {
      getState: () => {
        setRunningTask: (t: unknown) => void;
        setRunningTaskFor: (sid: string, t: unknown) => void;
      };
    };
    currentSessionId?: string;
  };
  const store = w.__sessionStore;
  const sid = w.currentSessionId || "";
  if (store?.getState && sid) {
    if (running) {
      const task = { session_id: sid, msg_id: "" };
      store.getState().setRunningTaskFor(sid, task);
    } else {
      store.getState().setRunningTaskFor(sid, null);
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
  badge: HTMLElement,
  text: string,
  klass: string,
  dotKlass: string,
): void {
  badge.innerHTML =
    '<span class="' +
    dotKlass +
    '" aria-hidden="true"></span>' +
    '<span class="badge-short">' +
    escapeHtml(text) +
    "</span>";
  badge.className = klass;
}

export function setStatusDotHealth(state: string): void {
  const badge = document.getElementById("statusBadge");
  if (!badge) return;
  const dot = badge.querySelector(".status-dot");
  if (!dot) return;
  dot.className = "status-dot" + (state ? " " + state : "");
}

export function updateSendBtn(): void {
  const sendBtn = document.getElementById("sendBtn");
  const stopBtn = document.getElementById("stopBtn");
  const badge = document.getElementById("statusBadge");
  if (!sendBtn || !stopBtn) return;

  if (!W.isRunning) {
    sendBtn.innerHTML = SVG_SEND;
    sendBtn.title = "Send message";
    sendBtn.className = "send-btn";
    stopBtn.style.display = "none";
  } else if (W.isPaused) {
    sendBtn.innerHTML = SVG_RESUME;
    sendBtn.title = "Resume";
    sendBtn.className = "send-btn paused-state";
    stopBtn.style.display = "flex";
    if (badge) renderStatusBadge(badge, "paused", "status-badge paused", "status-dot warn");
  } else {
    sendBtn.innerHTML = SVG_PAUSE;
    sendBtn.title = "Pause";
    sendBtn.className = "send-btn";
    stopBtn.style.display = "none";
    if (badge) renderStatusBadge(badge, "running", "status-badge", "status-dot ok");
  }
}

export function updatePauseBtn(): void {
  updateSendBtn();
}

export function updateStatus(status: string, source?: string): void {
  const badge = document.getElementById("statusBadge");
  if (!badge) return;
  const connected = status === "connected";
  const dotKlass = connected ? "status-dot ok" : "status-dot err";
  const text = connected ? source || "Local" : "disconnected";
  renderStatusBadge(
    badge,
    text,
    connected ? "status-badge" : "status-badge disconnected",
    dotKlass,
  );
  badge.title = connected
    ? source
      ? "connected · " + source
      : "connected · local worker"
    : "disconnected";
}

function escapeHtml(s: unknown): string {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ===== Channel / title helpers =================================== */

export function isPlaceholderTitle(title: string | null | undefined): boolean {
  if (!title) return true;
  if (title === "New conversation" || title === "Untitled") return true;
  return /^(wechat|discord|telegram|slack)\s*[:：]\s*\S{8,}/i.test(title);
}

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

export function channelPrefixFor(channel: string, accountId?: string): string {
  if (!channel) return "";
  const brand = channelBrand(channel);
  return accountId ? brand + " (" + accountId + ")" : brand;
}

export function displayTitleFor(conv: { title?: string } | null): string {
  if (!conv) return "";
  const t = (conv.title || "").trim();
  if (isPlaceholderTitle(t)) return "";
  return t.length > 30 ? t.slice(0, 30) + "…" : t;
}

export function refreshStatusSource(): void {
  const cid = W.currentSessionId ?? null;
  const conv = cid && W.conversations ? W.conversations[cid] : null;

  let ch: string | null = null;
  let acct: string | null = null;
  if (conv && conv.channel) {
    ch = conv.channel;
    acct = conv.account_id || null;
  } else if (W._pendingChannelChoice && W._pendingChannelChoice.channel) {
    ch = W._pendingChannelChoice.channel;
    acct = W._pendingChannelChoice.account_id || null;
  }

  const parts: string[] = [];
  if (ch) {
    parts.push(channelPrefixFor(ch, acct || undefined));
  } else if (conv && conv.source) {
    parts.push(conv.source);
  }
  updateStatus("connected", parts.join(" · "));

  if (ch && typeof W._startChannelHealthPoll === "function") {
    W._startChannelHealthPoll(ch, acct || "default");
  } else {
    W._stopChannelHealthPoll?.();
  }
}

/* ===== Pause / resume ============================================ */

export function onSendBtnClick(): void {
  if (W.isRunning) togglePause();
  else W.sendMessage?.();
}

export function togglePause(): void {
  const endpoint = W.isPaused ? "/api/resume" : "/api/pause";
  fetch(endpoint, { method: "POST" })
    .then((r) => r.json())
    .then((data) => {
      W.isPaused = data.paused;
      updateSendBtn();
    })
    .catch(() => {});
}

export function stopExecution(): void {
  if (!W.currentSessionId) {
    W.isPaused = false;
    W.isRunning = false;
    updateSendBtn();
    return;
  }
  fetch("/api/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: W.currentSessionId }),
  })
    .then((r) => r.json())
    .then(() => {
      W.isPaused = false;
      W.isRunning = false;
      updateSendBtn();
      W.addSystemMessage?.("Execution stopped.");
    })
    .catch(() => {
      W.isPaused = false;
      W.isRunning = false;
      updateSendBtn();
    });
}

/* ===== Thinking menu (legacy DOM — mostly no-op now) ============= */

export function buildThinkingMenu(): void {
  const cfg = W._thinkingConfig;
  if (!cfg) return;
  const menu = document.getElementById("thinkingMenu");
  const label = document.getElementById("thinkingLabel");
  const selector = document.getElementById("thinkingSelector");
  if (!menu || !label) return;

  const options = (cfg.options || []).slice();
  if (!options.length) {
    if (selector) selector.style.display = "none";
    menu.classList.remove("open");
    W._thinkingEffort = null;
    return;
  }
  if (selector) selector.style.display = "";

  let currentEffort = W._thinkingEffort;
  const values = options.map((o) => o.value);
  if (!currentEffort || values.indexOf(currentEffort) < 0) {
    currentEffort = cfg.default || values[0];
    W._thinkingEffort = currentEffort;
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

export function toggleThinkingMenu(e: Event): void {
  e.stopPropagation();
  const menu = document.getElementById("thinkingMenu");
  const sel = document.getElementById("thinkingSelector");
  if (!menu || !sel) return;
  const opening = !menu.classList.contains("open");
  if (opening) closeAllPopovers("thinking");
  menu.classList.toggle("open", opening);
  sel.classList.toggle("open", opening);
}

export function setThinkingEffort(level: string): void {
  W._thinkingEffort = level;
  buildThinkingMenu();
  document.getElementById("thinkingMenu")?.classList.remove("open");
  document.getElementById("thinkingSelector")?.classList.remove("open");
}

/* ===== Plus menu ================================================= */

export function togglePlusMenu(e?: Event): void {
  if (e) e.stopPropagation();
  const menu = document.getElementById("plusMenu");
  const btn = document.getElementById("plusBtn");
  if (!menu) return;
  const opening = !menu.classList.contains("open");
  if (opening) closeAllPopovers("plus");
  menu.classList.toggle("open", opening);
  if (btn) btn.classList.toggle("open", opening);
  if (opening) renderPlusMenu();
}

const PLUS_CHECK_SVG =
  '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">' +
  '<path d="M15.188 5.11a.5.5 0 0 1 .752.626l-.056.084-7.5 9a.5.5 0 0 1-.738.033l-3.5-3.5-.064-.078a.501.501 0 0 1 .693-.693l.078.064 3.113 3.113 7.15-8.58z"/>' +
  "</svg>";

export function renderPlusMenu(): void {
  const toolsItem = document.getElementById("plusMenuTools");
  const check = document.getElementById("plusMenuToolsCheck");
  if (toolsItem) toolsItem.classList.toggle("active", !!W._toolsEnabled);
  if (check) check.innerHTML = W._toolsEnabled ? PLUS_CHECK_SVG : "";

  const wsItem = document.getElementById("plusMenuWebSearch");
  const wsCheck = document.getElementById("plusMenuWebSearchCheck");
  const wsSub = document.getElementById("plusMenuWebSearchSub");
  if (wsItem) wsItem.classList.toggle("active", !!W._webSearchEnabled);
  if (wsCheck) wsCheck.innerHTML = W._webSearchEnabled ? PLUS_CHECK_SVG : "";
  if (wsSub) {
    const provName = W._webSearchProviderLabel || "";
    wsSub.textContent = provName ? " · " + provName : "";
  }
  updatePlusBtnIndicator();
}

export function updatePlusBtnIndicator(): void {
  const btn = document.getElementById("plusBtn");
  const anyActive = !!W._toolsEnabled || !!W._webSearchEnabled;
  if (btn) btn.classList.toggle("has-active", anyActive);
  renderActiveToolChips();
}

function renderActiveToolChips(): void {
  const host = document.getElementById("activeToolChips");
  if (!host) return;
  const escAttr = W.escAttr || ((s: unknown) => String(s));
  let chips = "";
  if (W._toolsEnabled) {
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
  if (W._webSearchEnabled) {
    let label = W._webSearchProviderLabel
      ? "Web Search · " + W._webSearchProviderLabel
      : "Web Search";
    if (W._webSearchProviderTier) label += " · " + W._webSearchProviderTier;
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
  W._toolsEnabled = !W._toolsEnabled;
  try {
    localStorage.setItem("agentic_tools_enabled", W._toolsEnabled ? "1" : "0");
  } catch {
    /* ignore */
  }
  updatePlusBtnIndicator();
}

export function toggleWebSearchEnabled(e?: Event): void {
  if (e) e.stopPropagation();
  W._webSearchEnabled = !W._webSearchEnabled;
  try {
    localStorage.setItem(
      "agentic_web_search_enabled",
      W._webSearchEnabled ? "1" : "0",
    );
  } catch {
    /* ignore */
  }
  if (W._webSearchEnabled && !W._webSearchProviderLabel) {
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
        W._webSearchProviderLabel = prov ? prov.name : "";
        W._webSearchProviderTier = prov && prov.tier ? prov.tier : "";
        renderPlusMenu();
      })
      .catch(() => {});
  } catch {
    /* ignore */
  }
}

/* ===== Detail panel ============================================== */

interface DetailNode {
  path: string;
  name: string;
  status: string;
  duration_ms?: number;
  prompt?: string;
  params?: Record<string, unknown>;
  output?: unknown;
  error?: string;
  node_type?: string;
  raw_reply?: unknown;
  attempts?: unknown[];
  expose?: string;
}

export function showDetail(node: DetailNode): void {
  W.selectedPath = node.path;
  const panel = document.getElementById("detailPanel");
  const title = document.getElementById("detailTitle");
  const body = document.getElementById("detailBody");
  if (!panel || !body) return;

  panel.classList.remove("collapsed");
  W.rightDock?.show("detail");
  if (title) title.textContent = node.name;

  const escHtml = W.escHtml || ((s: unknown) => String(s));
  const escAttr = W.escAttr || ((s: unknown) => String(s));
  const statusIcon =
    node.status === "success" ? "&#10003;" : node.status === "error" ? "&#10007;" : "&#9679;";
  const dur = (node.duration_ms || 0) > 0 ? Math.round(node.duration_ms!) + "ms" : "running...";

  let html =
    '<div class="detail-section"><div class="detail-section-title">Status</div>' +
    '<div class="detail-badge ' +
    node.status +
    '">' +
    statusIcon +
    " " +
    node.status +
    " &middot; " +
    dur +
    "</div></div>";

  html +=
    '<div class="detail-section"><div class="detail-section-title">Path</div>' +
    '<div class="detail-field-value">' +
    escHtml(node.path) +
    "</div></div>";

  if (node.prompt) {
    html +=
      '<div class="detail-section"><div class="detail-section-title">Prompt / Docstring</div>' +
      '<div class="detail-code">' +
      escHtml(node.prompt) +
      "</div></div>";
  }

  if (node.params && Object.keys(node.params).length > 0) {
    const dp: Record<string, unknown> = {};
    for (const dk in node.params) {
      if (dk !== "runtime" && dk !== "callback") dp[dk] = node.params[dk];
    }
    if (Object.keys(dp).length > 0) {
      html +=
        '<div class="detail-section"><div class="detail-section-title">Parameters</div>' +
        '<div class="detail-code">' +
        escHtml(JSON.stringify(dp, null, 2)) +
        "</div></div>";
    }
  }

  if (node.output != null) {
    html +=
      '<div class="detail-section"><div class="detail-section-title">Output</div>' +
      '<div class="detail-code">' +
      escHtml(
        typeof node.output === "string"
          ? node.output
          : JSON.stringify(node.output, null, 2),
      ) +
      "</div></div>";
  }

  if (node.error) {
    html +=
      '<div class="detail-section"><div class="detail-section-title">Error</div>' +
      '<div class="detail-code" style="color:var(--accent-red)">' +
      escHtml(node.error) +
      "</div></div>";
  }

  if (node.node_type === "exec") {
    const content = (node.params && node.params._content) || "";
    html +=
      '<div class="detail-section"><div class="detail-section-title">LLM Input</div>' +
      '<div class="detail-code">→ ' +
      escHtml(content) +
      "</div></div>";
    if (node.raw_reply != null) {
      html +=
        '<div class="detail-section"><div class="detail-section-title">LLM Reply</div>' +
        '<div class="detail-code">← ' +
        escHtml(node.raw_reply) +
        "</div></div>";
    }
  } else if (node.raw_reply != null) {
    html +=
      '<div class="detail-section"><div class="detail-section-title">Raw LLM Reply</div>' +
      '<div class="detail-code">' +
      escHtml(node.raw_reply) +
      "</div></div>";
  }

  if (node.attempts && node.attempts.length > 0) {
    html +=
      '<div class="detail-section"><div class="detail-section-title">Attempts (' +
      node.attempts.length +
      ')</div><div class="detail-code">' +
      escHtml(JSON.stringify(node.attempts, null, 2)) +
      "</div></div>";
  }

  html +=
    '<div class="detail-section"><div class="detail-section-title">Expose</div>' +
    '<div class="detail-field-value">' +
    escHtml(node.expose || "io") +
    "</div></div>";

  if (node.name !== "chat_session") {
    html +=
      '<div class="detail-section">' +
      "<button class=\"rerun-btn\" onclick=\"rerunFromNode('" +
      escAttr(node.path) +
      "')\">&#8634; Modify " +
      escHtml(node.name) +
      "</button></div>";
  }

  body.innerHTML = html;
}

export function closeDetail(): void {
  W.selectedPath = null;
  const panel = document.getElementById("detailPanel");
  if (!panel) return;
  panel.style.removeProperty("width");
  panel.classList.add("collapsed");
}

export function toggleDetail(): void {
  const panel = document.getElementById("detailPanel");
  if (!panel) return;
  if (!panel.classList.contains("collapsed")) {
    panel.style.removeProperty("width");
  }
  panel.classList.toggle("collapsed");
}

/* ===== Code viewer =============================================== */

export async function viewSource(name: string): Promise<void> {
  try {
    const resp = await fetch(
      "/api/function/" + encodeURIComponent(name) + "/source",
    );
    const data = await resp.json();
    if (data.error) {
      console.warn("[viewSource] " + name + ": " + data.error);
      return;
    }
    showCodeModal(name, data.source, data.category);
  } catch (e) {
    console.error("[viewSource] " + name + ":", e);
  }
}

export function showCodeModal(name: string, source: string, category?: string): void {
  let modal = document.getElementById("codeModal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "codeModal";
    modal.className = "code-modal-overlay";
    modal.innerHTML =
      '<div class="code-modal">' +
      '<div class="code-modal-header"><span class="code-modal-title" id="codeModalTitle"></span><button class="code-modal-close" onclick="closeCodeModal()">&times;</button></div>' +
      '<div class="code-modal-body"><pre id="codeModalPre"></pre></div>' +
      '<div class="code-modal-actions" id="codeModalActions"></div>' +
      "</div>";
    const m = modal;
    m.addEventListener("click", (e) => {
      if (e.target === m) closeCodeModal();
    });
    document.body.appendChild(modal);
  }
  const escAttr = W.escAttr || ((s: unknown) => String(s));
  const highlight = W.highlightPython || ((c: string) => c);
  document.getElementById("codeModalTitle")!.textContent = name;
  document.getElementById("codeModalPre")!.innerHTML = highlight(source);

  let actions = '<button class="code-modal-btn" onclick="closeCodeModal()">Close</button>';
  if (category !== "meta") {
    actions +=
      '<button class="code-modal-btn" onclick="editInModal(\'' +
      escAttr(name) +
      "')\">Edit</button>";
    actions +=
      '<button class="code-modal-btn" onclick="fixFromModal(\'' +
      escAttr(name) +
      "')\">Fix with LLM</button>";
  }
  document.getElementById("codeModalActions")!.innerHTML = actions;
  requestAnimationFrame(() => modal!.classList.add("active"));
}

export function closeCodeModal(): void {
  document.getElementById("codeModal")?.classList.remove("active");
}

export function editInModal(name: string): void {
  closeCodeModal();
  W.setInput?.("I want to edit function " + name);
}

export function fixFromModal(name: string): void {
  const instruction = prompt("What should be fixed in " + name + "?");
  if (!instruction) return;
  closeCodeModal();
  W.setInput?.("fix " + name + " " + instruction);
}

/* ===== Unified click-outside + plus-menu init ==================== */

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

try {
  W._toolsEnabled = localStorage.getItem("agentic_tools_enabled") === "1";
} catch {
  W._toolsEnabled = false;
}
setTimeout(() => updatePlusBtnIndicator(), 0);

/* ===== window bridges ============================================ */

W.setRunning = setRunning;
W.updateContextStats = updateContextStats;
W.setStatusDotHealth = setStatusDotHealth;
W.updateSendBtn = updateSendBtn;
W.updatePauseBtn = updatePauseBtn;
W.updateStatus = updateStatus;
W._isPlaceholderTitle = isPlaceholderTitle;
W._channelPrefixFor = channelPrefixFor;
W._displayTitleFor = displayTitleFor;
W.refreshStatusSource = refreshStatusSource;
W.onSendBtnClick = onSendBtnClick;
W.togglePause = togglePause;
W.stopExecution = stopExecution;
W.buildThinkingMenu = buildThinkingMenu;
W._closeAllPopovers = closeAllPopovers;
W.toggleThinkingMenu = toggleThinkingMenu;
W.setThinkingEffort = setThinkingEffort;
W.togglePlusMenu = togglePlusMenu;
W.renderPlusMenu = renderPlusMenu;
W._updatePlusBtnIndicator = updatePlusBtnIndicator;
W.toggleToolsEnabled = toggleToolsEnabled;
W.toggleWebSearchEnabled = toggleWebSearchEnabled;
W._refreshWebSearchProviderLabel = refreshWebSearchProviderLabel;
W.showDetail = showDetail;
W.closeDetail = closeDetail;
W.toggleDetail = toggleDetail;
W.viewSource = viewSource;
W.showCodeModal = showCodeModal;
W.closeCodeModal = closeCodeModal;
W.editInModal = editInModal;
W.fixFromModal = fixFromModal;
