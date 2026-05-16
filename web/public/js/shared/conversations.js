function _channelLabel(channel, accountId) {
  if (!channel) return 'local';
  return accountId ? channel + ':' + accountId : channel;
}

// Map a channel platform id to a brand-icon URL on simple-icons'
// public CDN. simple-icons ships official-mark SVGs for hundreds of
// platforms under an open license, intended exactly for "your app
// integrates with X" indicators. Each URL embeds the brand's own
// primary color so the icons read like the real platform — WeChat
// shows as its #07C160 green, Discord as its blurple, etc. Falls
// back to a single-letter chip if the icon fails to load.
var _CHANNEL_ICON_URL = {
  wechat:   'https://cdn.simpleicons.org/wechat/07C160',
  discord:  'https://cdn.simpleicons.org/discord/5865F2',
  telegram: 'https://cdn.simpleicons.org/telegram/26A5E4',
  slack:    'https://cdn.simpleicons.org/slack/4A154B',
};

// Channel health poller. When the active conv binds to a channel
// (wechat / discord / telegram / slack), poll the backend heartbeat
// endpoint every 5s and toggle the status-badge dot via
// `setStatusDotHealth(state)`. The badge *text* keeps showing
// "WeChat (xxx) · …" — only the dot reflects liveness.
//
// Backend semantics (see openprogram/webui/routes/channels.py):
//   alive=true            → adapter thread heartbeated within 30s → green
//   alive=false, unknown  → never seen (not started yet)          → yellow
//   alive=false, stale    → was alive, heartbeat went silent      → red
var _channelHealthTimer = null;
var _channelHealthKey = null;

function _stopChannelHealthPoll() {
  if (_channelHealthTimer) {
    clearInterval(_channelHealthTimer);
    _channelHealthTimer = null;
  }
  _channelHealthKey = null;
}
window._stopChannelHealthPoll = _stopChannelHealthPoll;

function _startChannelHealthPoll(channel, account_id) {
  var key = channel + ':' + (account_id || 'default');
  if (_channelHealthKey === key) return;  // already polling this one
  _stopChannelHealthPoll();
  _channelHealthKey = key;

  function _probe() {
    if (_channelHealthKey !== key) return;
    var url = '/api/channels/' + encodeURIComponent(channel)
            + '/' + encodeURIComponent(account_id || 'default') + '/status';
    fetch(url, { cache: 'no-store' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (_channelHealthKey !== key) return;
        if (typeof window.setStatusDotHealth !== 'function') return;
        var state = 'err';
        if (data.alive) state = 'ok';
        else if (data.state === 'unknown') state = 'warn';
        window.setStatusDotHealth(state);
      })
      .catch(function() {
        if (_channelHealthKey !== key) return;
        if (typeof window.setStatusDotHealth === 'function') {
          window.setStatusDotHealth('err');
        }
      });
  }
  _probe();
  _channelHealthTimer = setInterval(_probe, 5000);
}
window._startChannelHealthPoll = _startChannelHealthPoll;

function _channelIcon(plat) {
  var lc = String(plat || '').toLowerCase();
  var url = _CHANNEL_ICON_URL[lc];
  var letter = ((plat || '?')[0] || '?').toUpperCase();
  // The fallback letter chip is also what dropdown providers use, so
  // a broken icon still looks intentional rather than empty.
  var letterSpan = '<span class="provider-icon-letter">' + letter + '</span>';
  if (!url) return letterSpan;
  return '<img src="' + url + '" alt="" '
       + 'onerror="this.outerHTML=&quot;' + letterSpan.replace(/"/g, '&amp;quot;') + '&quot;">';
}
window._channelIcon = _channelIcon;

function renderSessions() {
  // React owns this rendering now (components/sidebar/sessions-list.tsx).
  // Early return so legacy callers (WS sessions_list handler, etc.)
  // don't fight the React reconciler by overwriting #convList with
  // innerHTML strings.
  return;
}
function _legacyRenderSessions_deprecated() {
  var container = document.getElementById('convList');
  var html = '';
  var convs = Object.values(conversations).sort(function(a, b) { return (b.created_at || 0) - (a.created_at || 0); });
  if (convs.length === 0) {
    html += '<div style="padding:8px 16px;font-size:12px;color:var(--text-muted)">No conversations yet</div>';
  } else {
    for (var ci = 0; ci < convs.length; ci++) {
      var c = convs[ci];
      var active = c.id === currentSessionId ? ' active' : '';
      // Build a clean display label: "<channel> (<account>) · <title>"
      // when the conv is bound to a channel; otherwise just the title.
      // Strip backend placeholder titles ("WeChat: o9cq..." etc.) so
      // the raw account id doesn't leak into the list.
      var prefix = (typeof window._channelPrefixFor === 'function') ?
                   window._channelPrefixFor(c.channel, c.account_id) : '';
      var realTitle = (typeof window._displayTitleFor === 'function') ?
                      window._displayTitleFor(c) : (c.title || '');
      // When the title is a backend placeholder, fall back to a
      // preview of the most recent user message so the user keeps
      // seeing some content. Pulled in from the server snapshot.
      if (!realTitle && c.preview) {
        var pv = String(c.preview).trim();
        realTitle = pv.length > 30 ? pv.slice(0, 30) + '…' : pv;
      }
      var label;
      if (prefix && realTitle)      label = prefix + ': ' + realTitle;
      else if (prefix)              label = prefix;
      else if (realTitle)           label = realTitle;
      else                          label = c.title || 'Untitled';
      html += '<div class="conv-item' + active + '" onclick="switchSession(\'' + c.id + '\')" title="' + escAttr(label) + '">' +
        '<span class="conv-title">' + escHtml(label) + '</span>' +
        '<span class="conv-del" onclick="event.stopPropagation();deleteSession(\'' + c.id + '\')" title="Delete"><svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="2" y1="2" x2="8" y2="8"/><line x1="8" y1="2" x2="2" y2="8"/></svg></span>' +
      '</div>';
    }
    html += '<div class="conv-clear-all" onclick="clearAllSessions()">Clear all</div>';
  }
  container.innerHTML = html;
}

// Cached list of channel accounts (filled lazily). Each entry:
// { channel, account_id, name, enabled, configured }.
var _channelAccountsCache = null;
var _channelAccountsPending = null;  // resolve fn for in-flight fetch

function fetchChannelAccounts() {
  if (_channelAccountsCache) return Promise.resolve(_channelAccountsCache);
  if (_channelAccountsPending) {
    return new Promise(function(res) {
      var prev = _channelAccountsPending;
      _channelAccountsPending = function(v) { prev(v); res(v); };
    });
  }
  return new Promise(function(res) {
    _channelAccountsPending = res;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'list_channel_accounts' }));
    } else {
      _channelAccountsPending = null;
      res([]);
    }
    setTimeout(function() {
      if (_channelAccountsPending === res) {
        _channelAccountsPending = null;
        res(_channelAccountsCache || []);
      }
    }, 3000);
  });
}

// Called by ws message handler when a channel_accounts envelope arrives.
function _onChannelAccountsMessage(rows) {
  _channelAccountsCache = Array.isArray(rows) ? rows : [];
  if (_channelAccountsPending) {
    var fn = _channelAccountsPending;
    _channelAccountsPending = null;
    fn(_channelAccountsCache);
  }
}
window._onChannelAccountsMessage = _onChannelAccountsMessage;

function _currentChannelChoice() {
  // For an existing conv, the badge reflects that conv's channel.
  // For a brand-new conv (no currentSessionId yet), it reflects the
  // pending choice that will be sent with the first message.
  if (currentSessionId && conversations[currentSessionId]) {
    var c = conversations[currentSessionId];
    return { channel: c.channel || null, account_id: c.account_id || null };
  }
  return window._pendingChannelChoice || { channel: null, account_id: null };
}

// The channel dropdown is the React <ChannelMenu /> now
// (components/chat/top-bar/channel-menu.tsx); it reuses the
// fetchChannelAccounts / _currentChannelChoice / _channelIcon data
// helpers below.
window.refreshChannelBadge = function() {
  // Channel state is shown by the existing #statusBadge via
  // refreshStatusSource; this hook just delegates so callers don't
  // need to know which renderer owns it.
  if (typeof window.refreshStatusSource === 'function') {
    window.refreshStatusSource();
  }
};


// ===== Branch (git-style) selector ============================
//
// Each leaf message in a session's DAG is a "branch tip". The
// session.head_id is the currently-checked-out branch. We expose:
//   - list_branches  → branches_list      (cached, refreshed lazily)
//   - checkout_branch → branch_checked_out (sets head_id)
//   - rename_branch / delete_branch_name (TODO UI)

var _branchesByConv = {};
window._branchesByConv = _branchesByConv;   // session_id → [{head_msg_id, name, active, ...}]
var _branchesPending = {};  // session_id → resolve fn

function fetchBranches(sessionId, opts) {
  if (!sessionId) return Promise.resolve([]);
  var force = !!(opts && opts.force);
  if (force) delete _branchesByConv[sessionId];
  if (_branchesByConv[sessionId]) return Promise.resolve(_branchesByConv[sessionId]);
  if (_branchesPending[sessionId]) {
    return new Promise(function(res) {
      var prev = _branchesPending[sessionId];
      _branchesPending[sessionId] = function(v) { prev(v); res(v); };
    });
  }
  return new Promise(function(res) {
    _branchesPending[sessionId] = res;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'list_branches', session_id: sessionId }));
    } else {
      delete _branchesPending[sessionId];
      res([]);
    }
    setTimeout(function() {
      if (_branchesPending[sessionId] === res) {
        delete _branchesPending[sessionId];
        res(_branchesByConv[sessionId] || []);
      }
    }, 3000);
  });
}

function _onBranchesListMessage(payload) {
  if (!payload || !payload.session_id) return;
  var rows = Array.isArray(payload.branches) ? payload.branches : [];
  _branchesByConv[payload.session_id] = rows;
  if (_branchesPending[payload.session_id]) {
    var fn = _branchesPending[payload.session_id];
    delete _branchesPending[payload.session_id];
    fn(rows);
  }
  if (payload.session_id === currentSessionId) {
    if (typeof window.refreshBranchBadge === 'function') window.refreshBranchBadge();
    if (typeof window.repaintBranchTags === 'function') window.repaintBranchTags();
    if (typeof window.renderBranchesPanel === 'function') window.renderBranchesPanel();
    // History DAG visualization (right rail): re-render whenever the
    // branches payload carries a fresh graph snapshot. This lets nodes
    // appear in real time the moment a user message (or assistant
    // reply) lands in the DB, without waiting for the next
    // load_session round-trip.
    if (Array.isArray(payload.graph) && typeof window.renderHistoryGraph === 'function') {
      try { window.renderHistoryGraph(payload.graph, payload.active || null); } catch (e) {}
      // Keep the in-memory conversation snapshot in sync too.
      if (conversations[payload.session_id]) {
        conversations[payload.session_id].graph = payload.graph;
        if (payload.active) conversations[payload.session_id].head_id = payload.active;
      }
    }
  }
}

// Right-sidebar Branches panel — third entry point for switching
// branches (besides the topbar chip dropdown and clicking a node in
// the history graph). Renders the same list with a collapsed/expanded
// toggle: collapsed shows just the active branch as a chip; expanded
// shows the whole list.
// Per-branch token usage cache. Keyed by session, then head_msg_id.
// Populated by _refreshBranchTokens off the batch endpoint, consumed
// by renderBranchesPanel to paint a "12K (6%)" suffix on each row.
var _branchTokensByConv = {};

function _formatBranchTokens(n) {
  if (!n) return '';
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000)    return (n / 1000).toFixed(1) + 'K';
  return String(n);
}

async function _refreshBranchTokens() {
  if (!currentSessionId) return;
  try {
    var r = await fetch('/api/sessions/' + encodeURIComponent(currentSessionId) + '/branches/tokens');
    if (!r.ok) return;
    var d = await r.json();
    var map = {};
    (d.branches || []).forEach(function (b) { map[b.head_id] = b; });
    _branchTokensByConv[currentSessionId] = map;
    if (typeof window.renderBranchesPanel === 'function') window.renderBranchesPanel();
  } catch (e) {}
}
window._refreshBranchTokens = _refreshBranchTokens;

// Inline-rename for a branch row — used by both the right-dock
// panel (renderBranchesPanel) and (eventually) the topbar dropdown.
// Replaces `nameEl`'s text with a focused <input>; Enter/blur commit,
// Esc/empty cancel. Empty submit is treated as cancel (consistent
// with the dropdown's behavior after the recent fix), not as an
// "AI auto-name" request.
// The branches panel is the React <BranchesPanel /> now
// (components/right-sidebar/branches-panel.tsx). This shim signals it
// to re-read `window._branchesByConv`.
window.renderBranchesPanel = function () {
  window.dispatchEvent(new Event('branches-updated'));
};
window._onBranchesListMessage = _onBranchesListMessage;

function _onBranchCheckedOut(payload) {
  if (!payload || !payload.ok || !payload.session_id) return;
  // Invalidate cache so the next dropdown re-fetches with the new
  // active marker. The server-side history graph / message list will
  // update through their own existing envelopes.
  delete _branchesByConv[payload.session_id];
  if (payload.session_id === currentSessionId && typeof window.refreshBranchBadge === 'function') {
    fetchBranches(payload.session_id).then(window.refreshBranchBadge);
  }
}
window._onBranchCheckedOut = _onBranchCheckedOut;

window.refreshBranchBadge = function() {
  var badge = document.getElementById('branchBadge');
  if (!badge) return;
  if (!currentSessionId) { badge.style.display = 'none'; return; }
  var list = _branchesByConv[currentSessionId] || [];
  // Show the chip even with a single branch — gives a stable place to
  // see the current branch name and (eventually) rename / split it.
  // Hidden only when the session has no branches at all (empty conv).
  if (list.length === 0) {
    badge.style.display = 'none';
    return;
  }
  var active = list.find(function(b) { return b.active; });
  var label = active ? active.name : 'detached';
  var nameEl = badge.querySelector('.branch-name');
  if (nameEl) {
    nameEl.textContent = label + ' (' + list.length + ')';
    // Cap width + ellipsis so a long auto-name doesn't blow up topbar.
    nameEl.style.display = 'inline-block';
    nameEl.style.maxWidth = '180px';
    nameEl.style.overflow = 'hidden';
    nameEl.style.textOverflow = 'ellipsis';
    nameEl.style.whiteSpace = 'nowrap';
    nameEl.style.verticalAlign = 'bottom';
  }
  badge.title = label + ' (' + list.length + ' branches)';
  badge.style.display = '';
};


// Session delete / clear-all + the confirm modal are React now
// (components/sidebar/sessions-list.tsx). switchSession is gone —
// the React sessions list navigates with the router directly.
function newSession() {
  if (window.location.pathname !== '/chat') {
    if (window.__navigate) { window.__navigate('/chat'); return; }
    window.location.href = '/chat';
    return;
  }
  // Already on /, reset in-place
  currentSessionId = null;
  history.replaceState(null, '', '/chat');
  // Point the React <MessageList /> at "no conversation" so it clears.
  // history.replaceState doesn't fire a Next.js route change, so the
  // app-shell pathname effect won't do this for us.
  try {
    if (window.__sessionStore) window.__sessionStore.getState().setCurrentConv(null);
  } catch (e) {}
  pendingResponses = {};
  trees = [];
  var container = document.getElementById('chatMessages');
  // Remove every child EXCEPT the React `#welcome-mount` placeholder
  // so the React `<WelcomeScreen />` portal stays alive. Wiping the
  // whole container would tear down the portal target and React
  // couldn't re-render the welcome panel into #chatMessages.
  if (container) {
    Array.from(container.children).forEach(function (ch) {
      // Keep both React portal hosts alive (welcome panel + message
      // stream). See `_clearChatMessages`.
      if (ch.id === 'welcome-mount' || ch.id === 'messages-mount') return;
      container.removeChild(ch);
    });
  }
  window._pendingChannelChoice = null;
  if (typeof window.refreshChannelBadge === 'function') window.refreshChannelBadge();
  setWelcomeVisible(true);
  renderSessions();
  // Clear the right-sidebar Branches panel — without this the previous
  // session's branch chip lingers on the welcome screen.
  if (typeof window.renderBranchesPanel === 'function') {
    try { window.renderBranchesPanel(); } catch (e) {}
  }
  // Also clear the right-sidebar History DAG graph. Without this an
  // empty `newSession()` (e.g. after deleting the current chat) leaves
  // the previous conversation's node graph rendered in the History
  // panel — the chat content is gone but the graph display lingers.
  if (typeof window.renderHistoryGraph === 'function') {
    try { window.renderHistoryGraph([], null); } catch (e) {}
  }
  var ctxEl = document.getElementById('contextStats');
  if (ctxEl) ctxEl.textContent = '';
  _hasActiveSession = false;
  var provBadge = document.getElementById('providerBadge');
  if (provBadge) {
    provBadge.textContent = provBadge.textContent.replace(' \ud83d\udd12', '');
  }
  var sessBadge = document.getElementById('sessionBadge');
  if (sessBadge) { sessBadge.textContent = 'no session'; sessBadge.title = ''; }
  loadProviders();
  loadAgentSettings();
  // Reset session-scoped chips that aren't covered by loadAgentSettings:
  // status badge (was showing previous session's "WeChat (xxx) · ...")
  // and branch chip (was showing previous session's branch list).
  if (typeof window.refreshStatusSource === 'function') window.refreshStatusSource();
  if (typeof window.refreshBranchBadge === 'function') {
    // Wipe local cache for the branch chip so it doesn't flash the old
    // session's branches before realising there's no current session.
    if (typeof _branchesByConv !== 'undefined') {
      // _branchesByConv is module-local in conversations.js — drop all keys.
      Object.keys(_branchesByConv).forEach(function (k) { delete _branchesByConv[k]; });
    }
    window.refreshBranchBadge();
  }
}

function loadSessionData(data) {
  if (!data.messages) data.messages = [];
  // Merge instead of replace so fields populated by an earlier
  // sessions_list (e.g. channel / account_id, which session_loaded
  // didn't always carry) survive when the load response lands.
  conversations[data.id] = Object.assign({}, conversations[data.id] || {}, data);
  renderSessions();
  // Reset branches panel to collapsed on every new conversation load.
  window._branchesPanelCollapsed = true;
  if (data.id === currentSessionId) {
    if (typeof window.refreshStatusSource === 'function') window.refreshStatusSource();
    if (typeof window.refreshChannelBadge === 'function') window.refreshChannelBadge();
    // Pull the latest branch list so the chip reflects this conv's
    // current head + alternates. fetchBranches caches per conv; we
    // invalidate to force a fresh server snapshot.
    delete _branchesByConv[data.id];
    fetchBranches(data.id).then(function() {
      if (typeof window.refreshBranchBadge === 'function') window.refreshBranchBadge();
    });
  }
  if (data.id === currentSessionId) {
    var area = document.getElementById('chatArea');
    var hasSavedScroll = !!sessionStorage.getItem('agentic_scroll');
    if (hasSavedScroll) _skipScrollToBottom = true;
    renderSessionMessages(data);
    if (data.function_trees && data.function_trees.length > 0) {
      for (var i = 0; i < data.function_trees.length; i++) {
        var ft = data.function_trees[i];
        if (ft && (ft.path || ft.name)) {
          trees.push(ft);
        }
      }
    }
    if (data.provider_info) {
      updateProviderBadge(data.provider_info);
    }
    // Refresh agent badges for this conversation's provider/model (was missing,
    // caused chat/exec badges to stay stale when switching between convs).
    loadAgentSettings();
    if (data.context_stats) {
      handleChatResponse(data.context_stats);
    } else {
      updateContextStats(data.messages || []);
    }
    var savedScroll = parseInt(sessionStorage.getItem('agentic_scroll') || '0', 10);
    if (area && savedScroll > 0) {
      requestAnimationFrame(function() {
        area.scrollTop = savedScroll;
        sessionStorage.removeItem('agentic_scroll');
      });
    }
  }
}

function extractMessagesFromTree(tree) {
  if (!tree || !tree.children) return [];
  var messages = [];
  for (var ci = 0; ci < tree.children.length; ci++) {
    var child = tree.children[ci];
    if (child.name === '_chat_query') {
      var query = child.params && child.params.query;
      if (query) {
        messages.push({ role: 'user', content: query });
      }
      if (child.output) {
        messages.push({ role: 'assistant', content: formatProgramResultContent(child.output), type: 'result', function: null });
      }
    } else if (child.name && child.name !== '_chat_query' && !child.name.startsWith('_')) {
      var funcName = child.name;
      var kwargs = child.params || {};
      var argStr = Object.entries(kwargs).filter(function(e) { return e[0] !== 'runtime'; }).map(function(e) { return e[0] + '=' + JSON.stringify(e[1]); }).join(' ');
      messages.push({ role: 'user', content: 'run ' + funcName + (argStr ? ' ' + argStr : ''), display: 'runtime' });
      if (child.output) {
        messages.push({ role: 'assistant', content: formatProgramResultContent(child.output), type: 'result', function: funcName, display: 'runtime' });
      }
    }
  }
  if (messages.length > 0) {
    for (var i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant') {
        messages[i].context_tree = tree;
        break;
      }
    }
  }
  return messages;
}

// Clear #chatMessages WITHOUT destroying the React `#welcome-mount`
// placeholder. `innerHTML = ''` would tear down the portal target, so
// the <WelcomeScreen /> could never render into it again (e.g. after
// loading a conversation and then clicking "New chat").
function _clearChatMessages(container) {
  Array.from(container.children).forEach(function (ch) {
    // Preserve the React portal hosts — `#welcome-mount` and
    // `#messages-mount` (the <MessageList /> portal). Removing them
    // would tear down the React render targets.
    if (ch.id === 'welcome-mount' || ch.id === 'messages-mount') return;
    container.removeChild(ch);
  });
}

function renderSessionMessages(conv) {
  var container = document.getElementById('chatMessages');
  trees = [];

  // Phase 3: mirror the loaded conversation into the React message
  // store. Dormant until the MessageList portal is mounted.
  if (typeof window.__feedStoreFromConv === 'function') {
    try { window.__feedStoreFromConv(conv); } catch (e) {}
  }

  if (!conv.messages || conv.messages.length === 0) {
    _clearChatMessages(container);
    setWelcomeVisible(true);
    return;
  }

  setWelcomeVisible(false);
  _clearChatMessages(container);

  // Phase 3: the React <MessageList /> renders the message bubbles now
  // (fed via `__feedStoreFromConv` above). The legacy DOM-building loop
  // is gone — only the non-render bookkeeping below still runs.

  // Expose the full message list to the nav module so it can walk
  // siblings without a round-trip. Populated here since this is the
  // only place we see the whole conversation at once.
  window._allMessages = conv.messages.slice();
  // Refresh the History DAG panel if it's wired up. The graph is the
  // full conversation (every branch), not just the HEAD chain, so
  // it comes from a separate field on the server payload.
  if (typeof window.renderHistoryGraph === 'function') {
    window.renderHistoryGraph(conv.graph || [], conv.head_id || null);
  }
  // Container-level run_active flag — CSS greys out Edit/Retry when
  // true. Flipped elsewhere when runs start / end; set it from the
  // snapshot we just loaded so initial state is right.
  var chatContainer = document.getElementById('chatMessages');
  if (chatContainer) {
    chatContainer.setAttribute(
      'data-run-active', conv.run_active ? 'true' : 'false',
    );
  }

  // Phase 3: legacy in-flight placeholder re-attachment is gone — the
  // React store keeps streaming bubbles alive across a conversation
  // re-render on its own, so there are no detached legacy nodes to
  // re-attach. `pendingResponses` is still drained so it doesn't grow
  // unbounded.
  try {
    var _runActive = (typeof window.isRunning !== 'undefined' && window.isRunning)
                  || (typeof isRunning !== 'undefined' && isRunning);
    if (!_runActive) {
      Object.keys(pendingResponses || {}).forEach(function (k) {
        delete pendingResponses[k];
      });
    }
  } catch (e) {}

  // Branch switch / checkout pivot: scroll to the message the user
  // clicked instead of the bottom of the new branch. Set by
  // history-graph.js / message-actions-nav.js before they fire
  // load_session.
  var pivot = window._postCheckoutScrollTo;
  if (pivot) {
    window._postCheckoutScrollTo = null;
    // Scope strictly to chatMessages — history-graph nodes in the
    // right sidebar ALSO carry data-msg-id, so a plain selector picks
    // the SVG node first and scrollIntoView jumps the wrong panel.
    var pivotEl = null;
    var key = window.CSS && CSS.escape ? CSS.escape(pivot) : pivot;
    var matches = container.querySelectorAll('[data-msg-id="' + key + '"], [data-msg-ids~="' + key + '"]');
    if (matches.length) pivotEl = matches[0];
    if (pivotEl) {
      requestAnimationFrame(function () {
        pivotEl.scrollIntoView({ behavior: 'auto', block: 'start' });
      });
      _skipScrollToBottom = false;
      return;
    }
  }

  if (!_skipScrollToBottom) scrollToBottom({ force: true });
  _skipScrollToBottom = false;
}

// --- Conversation message builders ---
// Phase 3: the legacy runtime-block / assistant-message DOM builders
// were removed — React <MessageList /> renders the conversation now.

function handleAttemptSwitched(data) {
  if (data.tree && (data.tree.path || data.tree.name)) {
    var rootKey = data.tree.path || data.tree.name;
    var idx = trees.findIndex(function(t) { return t.path === rootKey || t.name === data.tree.name; });
    if (idx >= 0) { trees[idx] = data.tree; } else { trees.push(data.tree); }
  }

  if (currentSessionId && conversations[currentSessionId]) {
    var conv = conversations[currentSessionId];
    var msgs = conv.messages || [];
    for (var i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'assistant' && msgs[i].function === data.function && msgs[i].attempts) {
        msgs[i].current_attempt = data.attempt_index;
        msgs[i].content = data.content;
        var restored = data.subsequent_messages || [];
        conv.messages = msgs.slice(0, i + 1).concat(restored);
        break;
      }
    }
    _skipScrollToBottom = true;
    renderSessionMessages(conv);
    var el = document.querySelector('[data-function="' + data.function + '"]');
    if (el) {
      requestAnimationFrame(function() { el.scrollIntoView({ block: 'center' }); });
    }
  }
}

// ===== Functions Panel =====

