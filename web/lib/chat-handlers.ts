/**
 * Chat-page WebSocket handlers.
 *
 * TS port of the legacy `public/js/chat/{init,chat-ws,chat}.js`. These
 * are the WS message handlers (`chat_ack` / `chat_response` / `status`
 * / `sessions_list` / `running_task`) plus the retry / follow-up glue.
 * `useWS` calls the exported functions directly; some are still bridged
 * onto `window.*` for inline-onclick HTML, React components and the
 * not-yet-migrated legacy scripts.
 *
 * Imported for side effects + `initChatPage()` by `useWS`.
 */

import {
  extractMessagesFromTree,
  fetchBranches,
  renderSessionMessages,
} from "./conversations";

interface ChatWindow {
  ws?: WebSocket | null;
  currentSessionId?: string | null;
  conversations?: Record<string, Record<string, unknown>>;
  pendingResponses?: Record<string, unknown>;
  isPaused?: boolean;
  isRunning?: boolean;
  _elapsedTimer?: ReturnType<typeof setInterval> | null;
  trees?: { path?: string; name?: string }[];
  _thinkingEffort?: string;
  _execThinkingEffort?: string;
  _branchesByConv?: Record<string, unknown>;
  _hasActiveSession?: boolean;
  _toolsEnabled?: boolean;
  _webSearchEnabled?: boolean;
  _lastRunCommand?: string | null;
  __sessionStore?: {
    getState: () => {
      setContextStats: (
        sid: string,
        t: { input: number; output: number; cache_read: number },
        ctx: number | null,
      ) => void;
    };
  };
  // Bridges to still-legacy modules.
  escHtml?: (s: unknown) => string;
  escAttr?: (s: unknown) => string;
  parseRunCommandForDisplay?: (t: string) => { funcName: string; params: string };
  scrollToBottom?: (opts?: { force?: boolean }) => void;
  setWelcomeVisible?: (show: boolean) => void;
  addSystemMessage?: (text: string) => void;
  setRunning?: (running: boolean) => void;
  updatePauseBtn?: () => void;
  loadAgentSettings?: () => void;
  loadProviders?: () => void;
  refreshChannelBadge?: () => void;
  refreshBranchBadge?: () => void;
  refreshStatusSource?: () => void;
  refreshTokenBadge?: () => void;
  _renderTokenBadge?: (data: unknown, sid: string) => void;
  _recordCacheWrite?: (sid: string) => void;
  refreshHistoryContextRange?: (sid: string) => void;
  _refreshBranchTokens?: () => void;
  _injectPauseRetryButtons?: () => void;
  _removePauseRetryButtons?: () => void;
  _updatePlusBtnIndicator?: () => void;
  _refreshWebSearchProviderLabel?: () => void;
  renderSessions?: () => void;
  [k: string]: unknown;
}

const W = window as unknown as ChatWindow;

/* ===== Run-active flag =========================================== */

// `data-run-active` on #chatMessages drives CSS greying-out of
// Edit/Retry while a run is in flight.
export function setRunActive(active: boolean): void {
  const c = document.getElementById("chatMessages");
  if (c) c.setAttribute("data-run-active", active ? "true" : "false");
}

/* ===== chat_ack / chat_response / status ========================= */

interface ChatAckData {
  session_id?: string;
  msg_id?: string;
}

export function wsHandleChatAck(data: ChatAckData): void {
  if (data.session_id) {
    const sid = data.session_id;
    W.currentSessionId = sid;
    if (window.location.pathname !== "/s/" + sid) {
      history.pushState(null, "", "/s/" + sid);
    }
    const convs = W.conversations || (W.conversations = {});
    if (!convs[sid]) {
      convs[sid] = { id: sid, title: "New conversation", messages: [] };
    }
    W.renderSessions?.();
    W.loadAgentSettings?.();
    W.refreshChannelBadge?.();
    // A fresh session never went through `load_session`, so fetch the
    // branch list now that the server registered the user turn.
    if (W._branchesByConv) delete W._branchesByConv[sid];
    fetchBranches(sid).then(() => {
      W.refreshBranchBadge?.();
    });
  }
  // A fresh chat_ack means a run just started — grey out Edit/Retry.
  setRunActive(true);
}

interface ChatResponseData {
  type?: string;
  [k: string]: unknown;
}

export function wsHandleChatResponse(data: ChatResponseData): void {
  // Cancelled envelope without a msg_id is the force-stop signal.
  if (data && data.type === "cancelled") {
    try {
      const rp = document.getElementById("runtime_pending");
      if (rp && rp.parentNode) rp.parentNode.removeChild(rp);
    } catch {
      /* ignore */
    }
    try {
      Object.keys(W.pendingResponses || {}).forEach((k) => {
        delete W.pendingResponses![k];
      });
    } catch {
      /* ignore */
    }
    setRunActive(false);
    W.setRunning?.(false);
    return;
  }
  handleChatResponse(data);
  if (data && (data.type === "result" || data.type === "error")) {
    setRunActive(false);
  }
}

interface StatusMsg {
  paused?: boolean;
  stopped?: boolean;
}

export function wsHandleStatus(msg: StatusMsg): void {
  W.isPaused = msg.paused;
  if (msg.stopped) {
    W.isRunning = false;
    if (W._elapsedTimer) {
      clearInterval(W._elapsedTimer);
      W._elapsedTimer = null;
    }
  }
  W.updatePauseBtn?.();
  if (msg.stopped) {
    W._removePauseRetryButtons?.();
  } else if (msg.paused) {
    W._injectPauseRetryButtons?.();
  } else {
    W._removePauseRetryButtons?.();
  }
}

/* ===== sessions_list / running_task ============================== */

interface SessionRow {
  id: string;
  title?: string;
  created_at?: number;
  has_session?: boolean;
  channel?: string | null;
  account_id?: string | null;
  peer?: string | null;
  peer_display?: string | null;
  source?: string | null;
  agent_id?: string | null;
  preview?: string | null;
}

export function handleSessionsList(data: SessionRow[]): void {
  const convs = W.conversations || (W.conversations = {});
  const serverIds = new Set((data || []).map((c) => c.id));
  Object.keys(convs).forEach((id) => {
    if (!serverIds.has(id)) delete convs[id];
  });
  if (data && data.length > 0) {
    for (const c of data) {
      if (!convs[c.id]) {
        convs[c.id] = {
          id: c.id,
          title: c.title,
          messages: [],
          created_at: c.created_at,
          has_session: c.has_session,
          channel: c.channel || null,
          account_id: c.account_id || null,
          peer: c.peer || null,
          peer_display: c.peer_display || null,
          source: c.source || null,
          agent_id: c.agent_id || null,
          preview: c.preview || null,
        };
      } else {
        convs[c.id].has_session = c.has_session;
        if ("channel" in c) convs[c.id].channel = c.channel || null;
        if ("account_id" in c) convs[c.id].account_id = c.account_id || null;
        if ("peer" in c) convs[c.id].peer = c.peer || null;
        if ("peer_display" in c) convs[c.id].peer_display = c.peer_display || null;
        if ("preview" in c) convs[c.id].preview = c.preview || null;
      }
    }
  }
  const sid = W.currentSessionId;
  if (sid && !convs[sid]) {
    newSessionImport();
  }
  W.renderSessions?.();
  if (sid && convs[sid] && convs[sid].has_session) {
    W._hasActiveSession = true;
    const provBadge = document.getElementById("providerBadge");
    if (provBadge && provBadge.textContent!.indexOf("\u{1F512}") === -1) {
      provBadge.textContent += " \u{1F512}";
    }
    W.loadProviders?.();
  }
}

// `newSession` lives in conversations.ts; call it lazily through
// window to avoid an import cycle (it's only hit on a stale id).
function newSessionImport(): void {
  (W.newSession as (() => void) | undefined)?.();
}

export function handleRunningTask(rt: unknown): void {
  if (rt) W.setRunning?.(true);
}

/* ===== handleChatResponse (bookkeeping) ========================== */

export function handleChatResponse(data: ChatResponseData): void {
  const type = data.type;

  if (type === "context_stats") {
    handleContextStats(data as ContextStatsData);
    return;
  }
  if (type === "status") {
    handleStatusResponse(data as StatusResponseData);
    return;
  }
  if (type === "follow_up_question") {
    handleFollowUpQuestion(data as { question?: string });
    return;
  }
  if (type === "stream_event" || type === "tree_update" || type === "user_message") {
    return;
  }

  // Final response (result / error / retry_result) -- task done.
  W.setRunning?.(false);
  W.loadAgentSettings?.();
  if (typeof W.refreshTokenBadge === "function") {
    try {
      W.refreshTokenBadge();
    } catch {
      /* ignore */
    }
  }
  const sid = W.currentSessionId;
  if (sid) {
    try {
      fetchBranches(sid, { force: true }).then(() => {
        try {
          W._refreshBranchTokens?.();
        } catch {
          /* ignore */
        }
      });
    } catch {
      /* ignore */
    }
  }

  if (W._elapsedTimer) {
    clearInterval(W._elapsedTimer);
    W._elapsedTimer = null;
  }

  const isRuntimeResult =
    data.display === "runtime" ||
    (!!data.function && data.function !== "chat");

  // Store assistant message.
  if (sid && W.conversations?.[sid]) {
    const conv = W.conversations[sid] as { messages?: Record<string, unknown>[]; title?: string };
    if (!conv.messages) conv.messages = [];
    const storedMsg: Record<string, unknown> = {
      role: "assistant",
      content: data.content || "",
      type,
      function: data.function || null,
      display: isRuntimeResult ? "runtime" : undefined,
      blocks:
        Array.isArray(data.blocks) && (data.blocks as unknown[]).length
          ? data.blocks
          : undefined,
    };
    if (type === "result" && data.function) {
      storedMsg.attempts = [
        {
          content: data.content || "",
          tree: data.context_tree || null,
          timestamp: Date.now() / 1000,
        },
      ];
      storedMsg.current_attempt = 0;
    }
    conv.messages.push(storedMsg);
    (W.updateContextStats as ((m: unknown[]) => void) | undefined)?.(conv.messages);

    // Conversation title.
    if (!conv.title || conv.title === "New conversation") {
      const msgs = conv.messages;
      if (msgs.length > 0) {
        conv.title = String((msgs[0].content as string) || "").slice(0, 50);
        W.renderSessions?.();
        W.refreshStatusSource?.();
      }
    }
  }
}

/* ===== context_stats ============================================= */

interface ContextStatsData {
  chat?: { input_tokens?: number; output_tokens?: number; cache_read?: number; cache_write?: number };
  input_tokens?: number;
  output_tokens?: number;
  cache_read?: number;
  cache_write_tokens?: number;
  context_window?: number;
  current_tokens?: number;
  naive_sum?: number;
  cache_hit_rate?: number;
  cache_read_total?: number;
  last_assistant_usage?: number;
  last_assistant_input?: number;
  last_assistant_cache_read?: number;
  last_turn_hit_rate?: number;
  input_total?: number;
  model?: string | null;
  source_mix?: unknown;
}

function handleContextStats(data: ContextStatsData): void {
  let chat = data.chat || {};
  if (!data.chat && (data.input_tokens || data.output_tokens)) {
    chat = {
      input_tokens: data.input_tokens || 0,
      output_tokens: data.output_tokens || 0,
      cache_read: data.cache_read || 0,
    };
  }
  const sid = W.currentSessionId;

  const cacheWrite = chat.cache_write || data.cache_write_tokens || 0;
  if (cacheWrite > 0 && sid) W._recordCacheWrite?.(sid);

  if (W.__sessionStore && sid) {
    try {
      W.__sessionStore.getState().setContextStats(
        sid,
        {
          input: chat.input_tokens || 0,
          output: chat.output_tokens || 0,
          cache_read: chat.cache_read || 0,
        },
        data.context_window || null,
      );
    } catch {
      /* store not ready — a later stats event lands */
    }
  }

  if (typeof W._renderTokenBadge === "function" && sid) {
    W._renderTokenBadge(
      {
        current_tokens:
          data.current_tokens ||
          (chat.input_tokens || 0) + (chat.output_tokens || 0),
        naive_sum: data.naive_sum || 0,
        context_window: data.context_window || 0,
        cache_hit_rate: data.cache_hit_rate || 0,
        cache_read_total: data.cache_read_total || chat.cache_read || 0,
        last_assistant_usage: data.last_assistant_usage || 0,
        last_assistant_input: data.last_assistant_input || 0,
        last_assistant_cache_read: data.last_assistant_cache_read || 0,
        last_turn_hit_rate: data.last_turn_hit_rate || 0,
        input_total: data.input_total || 0,
        model: data.model || null,
        source_mix: data.source_mix || null,
      },
      sid,
    );
  }

  if (sid) W.refreshHistoryContextRange?.(sid);
}

/* ===== status response =========================================== */

interface StatusResponseData {
  context_tree?: { path?: string; name?: string };
}

function handleStatusResponse(data: StatusResponseData): void {
  if (data.context_tree) {
    const ct = data.context_tree;
    const rootKey = ct.path || ct.name;
    const trees = W.trees || (W.trees = []);
    const idx = trees.findIndex((t) => t.path === rootKey || t.name === ct.name);
    if (idx >= 0) trees[idx] = ct;
    else trees.push(ct);
    const sid = W.currentSessionId;
    if (sid && W.conversations?.[sid]) {
      const conv = W.conversations[sid] as { messages?: unknown[] };
      conv.messages = extractMessagesFromTree(ct as never);
      renderSessionMessages(W.conversations[sid] as never);
    }
  }
  W.scrollToBottom?.();
}

/* ===== follow-up question ======================================== */

function handleFollowUpQuestion(data: { question?: string }): void {
  const pendingBlock = document.getElementById("runtime_pending");
  if (!pendingBlock) return;
  const contentArea =
    pendingBlock.querySelector(".runtime-block-content") ||
    pendingBlock.querySelector(".runtime-block-body");
  if (!contentArea) return;

  const existing = contentArea.querySelector(".follow-up-container");
  if (existing) existing.remove();

  const esc = W.escHtml || ((s: unknown) => String(s));
  const fuHtml =
    '<div class="follow-up-container" style="margin:12px 0;padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--bg-secondary)">' +
    '<div style="color:var(--accent-yellow);font-weight:600;margin-bottom:8px">&#9888; Follow-up Question</div>' +
    '<div style="margin-bottom:10px;color:var(--text-primary)">' +
    esc(data.question) +
    "</div>" +
    '<div style="display:flex;gap:8px">' +
    '<input type="text" id="followUpInput" placeholder="Type your answer..." ' +
    'style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:6px;background:var(--bg-primary);color:var(--text-primary);font-size:14px" ' +
    "onkeydown=\"if(event.key==='Enter')submitFollowUp()\">" +
    '<button onclick="submitFollowUp()" ' +
    'style="padding:8px 16px;border:none;border-radius:6px;background:var(--accent-blue);color:white;cursor:pointer;font-size:14px">Submit</button>' +
    "</div>" +
    "</div>";
  contentArea.insertAdjacentHTML("beforeend", fuHtml);
  const inp = document.getElementById("followUpInput") as HTMLInputElement | null;
  if (inp) inp.focus();
  W.scrollToBottom?.();
}

/* ===== follow-up submit ========================================== */

export function submitFollowUp(): void {
  const inp = document.getElementById("followUpInput") as HTMLInputElement | null;
  if (!inp) return;
  const answer = inp.value.trim();
  if (!answer) return;
  const container = inp.closest(".follow-up-container");
  if (container) container.remove();
  if (W.ws && W.ws.readyState === 1) {
    W.ws.send(
      JSON.stringify({
        action: "follow_up_answer",
        session_id: W.currentSessionId,
        answer,
      }),
    );
  }
}

/* ===== retry / pause-retry ======================================= */

// Per-node retry is the React <ExecutionTree /> retry panel now; the
// legacy ui.js node-detail panel still emits an onclick to this stub.
export function rerunFromNode(): void {}

export function injectPauseRetryButtons(): void {
  const esc = W.escAttr || ((s: unknown) => String(s));
  document.querySelectorAll(".runtime-block[data-function]").forEach((block) => {
    if (block.querySelector(".pause-retry-footer")) return;
    if (block.querySelector(".runtime-block-footer")) return;
    const fn = block.getAttribute("data-function");
    if (!fn) return;
    const footer = document.createElement("div");
    footer.className = "runtime-block-footer pause-retry-footer";
    footer.innerHTML =
      '<div class="runtime-footer-left">' +
      "<button class=\"rerun-btn\" onclick=\"stopAndRetry('" +
      esc(fn) +
      "')\">&#8634; Retry</button>" +
      "</div>";
    block.appendChild(footer);
  });
}

export function removePauseRetryButtons(): void {
  document.querySelectorAll(".pause-retry-footer").forEach((el) => {
    if (el.parentNode) el.parentNode.removeChild(el);
  });
}

export function stopAndRetry(funcName: string): void {
  const sid = W.currentSessionId;
  if (!sid) return;
  fetch("/api/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sid }),
  })
    .then((r) => r.json())
    .then(() => {
      W.isPaused = false;
      W.isRunning = false;
      (W.updateSendBtn as (() => void) | undefined)?.();
      setTimeout(() => retryCurrentBlock(funcName), 400);
    })
    .catch(() => {
      W.isPaused = false;
      W.isRunning = false;
      (W.updateSendBtn as (() => void) | undefined)?.();
    });
}

export function retryCurrentBlock(funcName: string): void {
  const sid = W.currentSessionId;
  if (!sid || !W.conversations?.[sid]) return;
  if (!W.ws || W.ws.readyState !== WebSocket.OPEN) {
    W.addSystemMessage?.("Retry failed: not connected to server.");
    return;
  }
  const parse =
    W.parseRunCommandForDisplay ||
    ((t: string) => ({ funcName: t, params: "" }));
  const esc = W.escHtml || ((s: unknown) => String(s));

  const msgs =
    ((W.conversations[sid] as { messages?: Record<string, unknown>[] }).messages) || [];
  let userCmd: string | null = null;

  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === "user" && msgs[i].display === "runtime") {
      const parsed = parse(String(msgs[i].content || ""));
      if (parsed.funcName === funcName || !funcName) {
        userCmd = String(msgs[i].original_content || msgs[i].content);
        break;
      }
    }
  }
  if (!userCmd) {
    for (let j = msgs.length - 1; j >= 0; j--) {
      if (msgs[j].role === "user") {
        const content = String(msgs[j].content || "");
        if (/^(run\s|create\s|fix\s)/i.test(content)) {
          const parsed2 = parse(content);
          if (!funcName || parsed2.funcName === funcName) {
            userCmd = String(msgs[j].original_content || content);
            break;
          }
        }
      }
    }
  }
  if (!userCmd && W._lastRunCommand) userCmd = W._lastRunCommand;
  if (!userCmd && funcName) userCmd = "run " + funcName;
  if (!userCmd) return;

  if (!funcName) {
    funcName = parse(userCmd).funcName || "";
  }

  let existingBlock: Element | null = funcName
    ? document.querySelector('.runtime-block[data-function="' + funcName + '"]')
    : null;
  if (!existingBlock) {
    existingBlock =
      document.querySelector(".runtime-block.error") ||
      document.querySelector(".runtime-block.interrupted");
  }
  if (existingBlock) {
    existingBlock.className = "runtime-block runtime-block-pending";
    existingBlock.id = "runtime_pending";
    existingBlock.setAttribute("data-function", funcName);
    const parsedDisplay = parse(userCmd);
    existingBlock.innerHTML =
      '<div class="runtime-block-header">' +
      '<span class="runtime-icon">&#9654;</span>' +
      '<span class="runtime-func">' +
      esc(parsedDisplay.funcName) +
      (parsedDisplay.params
        ? '(<span class="runtime-params">' + esc(parsedDisplay.params) + "</span>)"
        : "()") +
      "</span>" +
      "</div>" +
      '<div class="runtime-block-body"><div class="runtime-block-content">' +
      '<div class="typing-indicator"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>' +
      "</div></div>";
  }

  W.setRunning?.(true);
  W.ws.send(
    JSON.stringify({
      action: "retry_overwrite",
      session_id: sid,
      function: funcName,
      text: userCmd,
      thinking_effort: W._thinkingEffort,
      exec_thinking_effort: W._execThinkingEffort,
    }),
  );
}

/* ===== assistant message (programs-panel toast) ================== */

export function addAssistantMessage(text: string): void {
  W.setWelcomeVisible?.(false);
  // The legacy bubble DOM is dropped (React owns the stream); this is
  // kept only so programs-panel.js's delete-function toast doesn't
  // throw. A real React toast can replace it later.
  void text;
}

/* ===== page init ================================================= */

export function initChatPage(): void {
  // Re-derive currentSessionId from the URL on every chat-page mount.
  const m = window.location.pathname.match(/^\/s\/([^/]+)/);
  W.currentSessionId = m ? m[1] : null;

  W.loadProviders?.();
  if (!window.location.pathname.match(/^\/s\//)) {
    W.setWelcomeVisible?.(true);
  }

  // Rehydrate the tools chip flags from localStorage.
  try {
    if (localStorage.getItem("agentic_tools_enabled") === "1") {
      W._toolsEnabled = true;
    }
    if (localStorage.getItem("agentic_web_search_enabled") === "1") {
      W._webSearchEnabled = true;
    }
  } catch {
    /* ignore */
  }
  W._updatePlusBtnIndicator?.();
  W._refreshWebSearchProviderLabel?.();
}

// beforeunload — persist scroll position. Installed once.
window.addEventListener("beforeunload", () => {
  const area = document.getElementById("chatArea");
  if (area) sessionStorage.setItem("agentic_scroll", String(area.scrollTop));
});

/* ===== window bridges ============================================ */

W.setRunActive = setRunActive;
W._wsHandleChatAck = wsHandleChatAck;
W._wsHandleChatResponse = wsHandleChatResponse;
W._wsHandleStatus = wsHandleStatus;
W._handleSessionsList = handleSessionsList;
W._handleRunningTask = handleRunningTask;
W.handleChatResponse = handleChatResponse;
W.submitFollowUp = submitFollowUp;
W.rerunFromNode = rerunFromNode;
W._injectPauseRetryButtons = injectPauseRetryButtons;
W._removePauseRetryButtons = removePauseRetryButtons;
W.stopAndRetry = stopAndRetry;
W.retryCurrentBlock = retryCurrentBlock;
W.addAssistantMessage = addAssistantMessage;
