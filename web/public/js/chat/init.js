// ===== WebSocket Connection =====

// Re-derive currentSessionId from the URL on every mount. state.js only reads it
// once at module load; SPA navigations between /c/{a} and /c/{b} don't re-run
// it, so without this the second conversation would load with the first id.
(function _syncConvIdFromPath() {
  var m = window.location.pathname.match(/^\/s\/([^/]+)/);
  currentSessionId = m ? m[1] : null;
})();

// ContextGit: data-run-active on the chat container drives CSS
// greying-out of Edit/Retry buttons while an agent run is in flight.
// conversations.js sets the initial state on load; we flip it here
// when chat_ack (start) and chat_response terminal types arrive.
function setRunActive(active) {
  var c = document.getElementById('chatMessages');
  if (c) c.setAttribute('data-run-active', active ? 'true' : 'false');
}
// Exposed so retry / edit POST handlers can flip it immediately —
// those paths don't get a chat_ack, so init.js's WS handler can't
// see them start.
window.setRunActive = setRunActive;

// WebSocket lifecycle (connect / reconnect) is owned by the React
// `useWS` hook now — see web/lib/use-ws.ts.

// WS message dispatch is owned by the React `useWS` hook now
// (web/lib/use-ws.ts). The three handlers below are the last legacy
// ones still called from there; they migrate in slice F.

window._wsHandleChatAck = function (data) {
  var msg = { data: data };
      if (msg.data.session_id) {
        currentSessionId = msg.data.session_id;
        window.currentSessionId = currentSessionId;
        // Update URL to /c/{session_id} without full page reload
        if (window.location.pathname !== '/s/' + currentSessionId) {
          history.pushState(null, '', '/s/' + currentSessionId);
        }
        if (!conversations[currentSessionId]) {
          conversations[currentSessionId] = { id: currentSessionId, title: 'New conversation', messages: [] };
        }
        renderSessions();
        // Refresh badges — conversation's provider may differ from default
        loadAgentSettings();
        if (typeof window.refreshChannelBadge === 'function') window.refreshChannelBadge();
        // Branches: a fresh session never went through `load_session`,
        // so the right-rail Branches panel stays empty until the user
        // refreshes. Fetch the branch list now (now that the server
        // has registered the user turn) and render the panel.
        if (typeof fetchBranches === 'function') {
          if (typeof _branchesByConv !== 'undefined' && _branchesByConv) {
            delete _branchesByConv[currentSessionId];
          }
          fetchBranches(currentSessionId).then(function () {
            if (typeof window.renderBranchesPanel === 'function') window.renderBranchesPanel();
            if (typeof window.refreshBranchBadge === 'function') window.refreshBranchBadge();
          });
        }
      }
      // Stamp the server msg_id onto the optimistically-rendered user
      // bubble so retry/branch buttons can target it.
      if (msg.data.msg_id && window._pendingUserBubble) {
        window._pendingUserBubble.setAttribute('data-msg-id', msg.data.msg_id);
        window._pendingUserBubble = null;
      }
      // chat.js created the assistant placeholder under a temporary
      // "pending_<ts>" key (server msg_id wasn't known yet). Rekey
      // it to the real msg_id now so stream_event / chat_response
      // can look the bubble up exactly instead of guessing first.
      if (msg.data.msg_id && typeof pendingResponses !== 'undefined') {
        var _serverMsgId = msg.data.msg_id;
        if (!pendingResponses[_serverMsgId]) {
          var _tempKeys = Object.keys(pendingResponses).filter(function (k) {
            return k.indexOf('pending_') === 0;
          });
          if (_tempKeys.length === 1) {
            pendingResponses[_serverMsgId] = pendingResponses[_tempKeys[0]];
            delete pendingResponses[_tempKeys[0]];
          }
        }
      }
      // ContextGit: a fresh chat_ack means a run just started.
      // Flip the container flag so Edit/Retry grey out until the
      // run finishes (signalled by chat_response / error / result).
      setRunActive(true);
};

window._wsHandleChatResponse = function (data) {
  var msg = { data: data };
      // Cancelled envelope without a msg_id is the force-stop signal
      // from /api/stop. Clear every in-flight placeholder + the
      // running_task ghost bubble in one shot, then fall through so
      // handleChatResponse still gets to render the "stopped" notice
      // (if any pending bubble matches a msg_id it carries).
      if (msg.data && msg.data.type === 'cancelled') {
        try {
          var _rp = document.getElementById('runtime_pending');
          if (_rp && _rp.parentNode) _rp.parentNode.removeChild(_rp);
        } catch (e) {}
        try {
          Object.keys(pendingResponses || {}).forEach(function (k) {
            var ph = pendingResponses[k];
            if (ph && ph.parentNode) ph.parentNode.removeChild(ph);
            delete pendingResponses[k];
          });
        } catch (e) {}
        setRunActive(false);
        if (typeof setRunning === 'function') setRunning(false);
        return;
      }
      handleChatResponse(msg.data);
      // Terminal response types signal the run is finished — lift
      // the Edit/Retry grey-out. 'streaming' / 'delta' types leave
      // the flag on because more is still coming.
      if (msg.data && (msg.data.type === 'result' || msg.data.type === 'error')) {
        setRunActive(false);
      }
};

window._wsHandleStatus = function (msg) {
      isPaused = msg.paused;
      if (msg.stopped) {
        isRunning = false;
        // Optimistically mark every still-running node as cancelled.
        // The worker thread will broadcast the authoritative tree_update
        // momentarily, but without this step the tree flashes "running"
        // (blue pulse) between the stop ack and the worker's final emit.
        function _markCancelled(node) {
          if (!node) return;
          if (node.status === 'running') {
            node.status = 'error';
            if (!node.error) node.error = 'Cancelled by user';
            if (!node.end_time) node.end_time = Date.now() / 1000;
          }
          if (node.children) node.children.forEach(_markCancelled);
        }
        try { (trees || []).forEach(_markCancelled); } catch(e) {}
        try {
          Object.keys(_nodeCache || {}).forEach(function(k) { _markCancelled(_nodeCache[k]); });
        } catch(e) {}
        // Tear down the elapsed-time ticker and strip data-running flags so
        // the frozen durations stop being overwritten.
        if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
        document.querySelectorAll('.node-duration[data-running]').forEach(function(el) {
          el.removeAttribute('data-running');
        });
        // Optimistically finalize the in-progress runtime block: drop the
        // typing-indicator, flip the tree header icon from pulsing to idle,
        // and inject a footer with Retry button. The worker's final `result`
        // broadcast may arrive late (or not at all if the CLI subprocess
        // takes time to die) — without this, the block stays stuck at
        // "... three dots" with a blue pulse forever.
        document.querySelectorAll('.runtime-block[data-function]').forEach(function(block) {
          var ti = block.querySelector('.typing-indicator');
          if (ti && ti.parentNode) ti.parentNode.removeChild(ti);
          if (block.id === 'runtime_pending') block.id = '';
          var treeHdr = block.querySelector('.inline-tree-header > span:first-child');
          if (treeHdr) {
            treeHdr.innerHTML = '<span style="color:var(--accent-cyan)">&#9670;</span> Execution Tree';
          }
          if (!block.querySelector('.runtime-block-footer')) {
            var fn = block.getAttribute('data-function');
            var footer = document.createElement('div');
            footer.className = 'runtime-block-footer';
            footer.innerHTML = '<div class="runtime-footer-left">' +
              '<button class="rerun-btn" onclick="retryCurrentBlock(\'' + escAttr(fn) + '\')">&#8634; Retry</button>' +
            '</div><div class="runtime-footer-center"></div><div class="runtime-footer-right"></div>';
            block.appendChild(footer);
          }
        });
      }
      updatePauseBtn();
      refreshInlineTrees();
      if (msg.stopped) {
        _removePauseRetryButtons();
      } else if (msg.paused) {
        _injectPauseRetryButtons();
      } else {
        _removePauseRetryButtons();
      }
};

function _handleSessionsList(data) {
  var serverIds = new Set((data || []).map(function(c) { return c.id; }));
  Object.keys(conversations).forEach(function(id) {
    if (!serverIds.has(id)) delete conversations[id];
  });
  if (data && data.length > 0) {
    for (var ci = 0; ci < data.length; ci++) {
      var c = data[ci];
      if (!conversations[c.id]) {
        conversations[c.id] = {
          id: c.id, title: c.title, messages: [],
          created_at: c.created_at, has_session: c.has_session,
          channel: c.channel || null,
          account_id: c.account_id || null,
          peer: c.peer || null,
          peer_display: c.peer_display || null,
          source: c.source || null,
          agent_id: c.agent_id || null,
          preview: c.preview || null,
        };
      } else {
        conversations[c.id].has_session = c.has_session;
        if ('channel' in c) conversations[c.id].channel = c.channel || null;
        if ('account_id' in c) conversations[c.id].account_id = c.account_id || null;
        if ('peer' in c) conversations[c.id].peer = c.peer || null;
        if ('peer_display' in c) conversations[c.id].peer_display = c.peer_display || null;
        if ('preview' in c) conversations[c.id].preview = c.preview || null;
      }
    }
  }
  if (currentSessionId && !conversations[currentSessionId]) {
    newSession();
  }
  renderSessions();
  if (currentSessionId && conversations[currentSessionId] && conversations[currentSessionId].has_session) {
    _hasActiveSession = true;
    var provBadge = document.getElementById('providerBadge');
    if (provBadge && provBadge.textContent.indexOf('\ud83d\udd12') === -1) {
      provBadge.textContent += ' \ud83d\udd12';
    }
    loadProviders();
  }
}

function _handleRunningTask(rt) {
  // Phase 3: an in-flight run on reconnect / load now surfaces through
  // the React message store (chat-stream reducer's stream_event /
  // tree_update). The legacy ghost-bubble / runtime-block DOM builder
  // is retired — just flip the running flag.
  if (rt) setRunning(true);
}

// (toggleConvList, toggleFavList, doRefreshFunctions are now React state in components/sidebar/sidebar.tsx)
// ===== Column Resize =====
//
// Sidebar / detail column drag handles are now wired up by
// `useColResize()` in `web/lib/use-col-resize.ts`, invoked from
// `app-shell.tsx`. The legacy IIFE has been removed.

// (Panel resize removed — single conversations list now)

// ===== Event Listeners =====

// Thinking menu close-on-outside now handled by unified popover logic in ui.js

// The chat textarea + function form both live in the React Composer
// (web/components/chat/composer.tsx, web/components/chat/fn-form.tsx).
// Enter / Escape / Cmd-Enter handling is in those components; init.js
// no longer wires anything to the input wrapper.

// Keepalive ping is owned by the React `useWS` hook now.

// ===== Lifecycle =====
window.addEventListener('beforeunload', function() {
  var area = document.getElementById('chatArea');
  if (area) sessionStorage.setItem('agentic_scroll', area.scrollTop);
});

// ===== Init =====
// (socket opened by the React `useWS` hook)
loadProviders();
// Only show welcome on /new, not on /c/{id}
if (!window.location.pathname.match(/^\/s\//)) {
  setWelcomeVisible(true);
}

// Re-render tools chip + plus-button indicator on every chat-page mount.
// ui.js's initPlusMenu IIFE runs once when shared scripts load, which can be
// before #activeToolChips exists (or on SPA nav it simply never re-fires),
// so the chip would go missing after a refresh even though _toolsEnabled was
// persisted to localStorage.
(function _rehydrateToolsUI() {
  try {
    if (localStorage.getItem('agentic_tools_enabled') === '1') {
      window._toolsEnabled = true;
    }
    if (localStorage.getItem('agentic_web_search_enabled') === '1') {
      window._webSearchEnabled = true;
    }
  } catch (_) {}
  if (typeof _updatePlusBtnIndicator === 'function') {
    _updatePlusBtnIndicator();
  }
  // Prefetch the user's configured default-search-provider label so the
  // chip/menu can read "Web Search · Tavily" on first paint instead of
  // showing a bare label until the user opens the menu.
  if (typeof _refreshWebSearchProviderLabel === 'function') {
    _refreshWebSearchProviderLabel();
  }
})();

// `__triggerPendingRunFunction` was removed when the
// `/programs → /chat` hand-off was migrated to a React effect in
// page-shell.tsx. The `?run=NAME` / `window.__pendingRunFunction`
// entry points still work — the React effect handles both, polling
// `window.availableFunctions` (still legacy-populated) until the
// `functions_list` WS envelope arrives.
