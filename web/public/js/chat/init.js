// ===== WebSocket Connection =====

// Re-derive currentConvId from the URL on every mount. state.js only reads it
// once at module load; SPA navigations between /c/{a} and /c/{b} don't re-run
// it, so without this the second conversation would load with the first id.
(function _syncConvIdFromPath() {
  var m = window.location.pathname.match(/^\/c\/([^/]+)/);
  currentConvId = m ? m[1] : null;
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

function connect() {
  var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws');

  ws.onopen = function() {
    updateStatus('connected');
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null;
    }
    // currentConvId already derived from URL in state.js — send agent_settings
    // with that value so badges reflect the correct conversation from the start.
    loadAgentSettings();
    ws.send(JSON.stringify({ action: 'list_conversations' }));
    if (currentConvId) {
      ws.send(JSON.stringify({ action: 'load_conversation', conv_id: currentConvId }));
    }
  };

  ws.onmessage = function(e) {
    try {
      var msg = JSON.parse(e.data);
      handleMessage(msg);
    } catch(err) {
      console.error('[ws.onmessage] error:', err);
    }
  };

  ws.onclose = function() {
    updateStatus('disconnected');
    reconnectTimer = setTimeout(connect, 2000);
  };

  ws.onerror = function() { ws.close(); };
}

function handleMessage(msg) {
  switch (msg.type) {
    case 'full_tree':
      trees = msg.data || [];
      break;
    case 'event':
      handleContextEvent(msg.event, msg.data);
      break;
    case 'functions_list':
      availableFunctions = msg.data || [];
      loadProgramsMeta().then(function() { renderFunctions(); });
      break;
    case 'history_list':
      (msg.data || []).forEach(function(c) {
        conversations[c.id] = conversations[c.id] || { id: c.id, title: c.title, messages: [] };
      });
      renderConversations();
      break;
    case 'chat_ack':
      if (msg.data.conv_id) {
        currentConvId = msg.data.conv_id;
        window.currentConvId = currentConvId;
        // Update URL to /c/{conv_id} without full page reload
        if (window.location.pathname !== '/c/' + currentConvId) {
          history.pushState(null, '', '/c/' + currentConvId);
        }
        if (!conversations[currentConvId]) {
          conversations[currentConvId] = { id: currentConvId, title: 'New conversation', messages: [] };
        }
        renderConversations();
        // Refresh badges — conversation's provider may differ from default
        loadAgentSettings();
        if (typeof window.refreshChannelBadge === 'function') window.refreshChannelBadge();
      }
      // Stamp the server msg_id onto the optimistically-rendered user
      // bubble so retry/branch buttons can target it.
      if (msg.data.msg_id && window._pendingUserBubble) {
        window._pendingUserBubble.setAttribute('data-msg-id', msg.data.msg_id);
        window._pendingUserBubble = null;
      }
      // ContextGit: a fresh chat_ack means a run just started.
      // Flip the container flag so Edit/Retry grey out until the
      // run finishes (signalled by chat_response / error / result).
      setRunActive(true);
      break;
    case 'chat_response':
      handleChatResponse(msg.data);
      // Terminal response types signal the run is finished — lift
      // the Edit/Retry grey-out. 'streaming' / 'delta' types leave
      // the flag on because more is still coming.
      if (msg.data && (msg.data.type === 'result' || msg.data.type === 'error' ||
                       msg.data.type === 'cancelled')) {
        setRunActive(false);
      }
      break;
    case 'conversation_loaded':
      loadConversationData(msg.data);
      break;
    case 'conversation_reload':
      if (msg.data && msg.data.conv_id === currentConvId && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: 'load_conversation', conv_id: currentConvId }));
      }
      break;
    case 'attempt_switched':
      handleAttemptSwitched(msg.data);
      break;
    case 'conversations_list':
      _handleConversationsList(msg.data);
      break;
    case 'channel_accounts':
      if (typeof window._onChannelAccountsMessage === 'function') {
        window._onChannelAccountsMessage(msg.data);
      }
      break;
    case 'branches_list':
      if (typeof window._onBranchesListMessage === 'function') {
        window._onBranchesListMessage(msg.data);
      }
      break;
    case 'branch_checked_out':
      if (typeof window._onBranchCheckedOut === 'function') {
        window._onBranchCheckedOut(msg.data);
      }
      break;
    case 'branch_renamed':
    case 'branch_name_deleted':
      if (msg.data && msg.data.conv_id && typeof window._onBranchesListMessage === 'function') {
        // Force a refetch so the badge picks up the new label.
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ action: 'list_branches', conv_id: msg.data.conv_id }));
        }
      }
      break;
    case 'conversation_channel_updated':
      if (msg.data && msg.data.ok && msg.data.conv_id && conversations[msg.data.conv_id]) {
        conversations[msg.data.conv_id].channel = msg.data.channel || null;
        conversations[msg.data.conv_id].account_id = msg.data.account_id || null;
        conversations[msg.data.conv_id].peer = msg.data.peer || null;
        renderConversations();
        if (msg.data.conv_id === currentConvId) {
          if (typeof window.refreshStatusSource === 'function') window.refreshStatusSource();
          if (typeof window.refreshChannelBadge === 'function') window.refreshChannelBadge();
        }
      }
      break;
    case 'status':
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
      break;
    case 'running_task':
      _handleRunningTask(msg.data);
      break;
    case 'provider_info':
    case 'provider_changed':
      updateProviderBadge(msg.data);
      loadProviders();
      if (msg.type === 'provider_changed') {
        addSystemMessage('Switched to ' + formatProviderLabel(msg.data));
      }
      break;
    case 'agent_settings_changed':
      _agentSettings.chat = msg.data.chat || _agentSettings.chat;
      _agentSettings.exec = msg.data.exec || _agentSettings.exec;
      updateAgentBadges();
      loadAgentSettings();
      addSystemMessage('Agent settings updated: Chat=' + msg.data.chat.provider + '\u00b7' + msg.data.chat.model + ', Exec=' + msg.data.exec.provider + '\u00b7' + msg.data.exec.model);
      break;
    case 'chat_session_update':
      if (msg.data && msg.data.session_id && _agentSettings.chat) {
        _agentSettings.chat.session_id = msg.data.session_id;
        updateAgentBadges();
      }
      break;
    case 'pong':
      break;
  }
}

function handleContextEvent(eventType, data) {
  updateTreeData(data);
}

function _handleConversationsList(data) {
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
  if (currentConvId && !conversations[currentConvId]) {
    newConversation();
  }
  renderConversations();
  if (currentConvId && conversations[currentConvId] && conversations[currentConvId].has_session) {
    _hasActiveSession = true;
    var provBadge = document.getElementById('providerBadge');
    if (provBadge && provBadge.textContent.indexOf('\ud83d\udd12') === -1) {
      provBadge.textContent += ' \ud83d\udd12';
    }
    loadProviders();
  }
}

function _handleRunningTask(rt) {
  if (!rt) return;
  setRunning(true);

  // Chat query
  if (rt.func_name === '_chat') {
    if (!document.getElementById('runtime_pending')) {
      var chatDiv = document.createElement('div');
      chatDiv.className = 'message bot';
      chatDiv.id = 'runtime_pending';
      chatDiv.innerHTML =
        '<div class="message-header">' +
          '<div class="message-avatar bot-avatar">A</div>' +
          '<div class="message-sender">Agentic</div>' +
        '</div>' +
        '<div class="message-content">' +
          '<div class="typing-indicator"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>' +
        '</div>';
      appendToChat(chatDiv);
    }
    return;
  }

  // Remove interrupted blocks
  var interruptedBlock = document.querySelector('.runtime-block.interrupted[data-function="' + rt.func_name + '"]');
  if (interruptedBlock) interruptedBlock.remove();
  if (!interruptedBlock) {
    var allInterrupted = document.querySelectorAll('.runtime-block.interrupted');
    for (var ii = 0; ii < allInterrupted.length; ii++) allInterrupted[ii].remove();
  }

  // Check if there's already a completed block for this function (retry/modify scenario)
  var existingBlock = document.querySelector('.runtime-block[data-function="' + rt.func_name + '"]');
  var isRetryOfExisting = existingBlock && !existingBlock.classList.contains('runtime-block-pending');

  if (!document.getElementById('runtime_pending')) {
    var paramsStr = rt.display_params || '';
    var hdr = '<div class="runtime-block-header" onclick="toggleRuntimeBlock(this)">' +
      '<span class="runtime-icon">&#9654;</span>' +
      '<span class="runtime-func">' + escHtml(rt.func_name) +
        (paramsStr ? '(<span class="runtime-params">' + escHtml(paramsStr) + '</span>)' : '()') +
      '</span>' +
    '</div>';

    var bodyContent = '<div class="typing-indicator"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>';
    var treeHtml = '';
    if (rt.partial_tree && (rt.partial_tree.path || rt.partial_tree.name)) {
      var treeId = 'itree_running_' + rt.func_name.replace(/[^a-zA-Z0-9]/g, '_');
      treeHtml = renderInlineTree(rt.partial_tree, treeId);
      updateTreeData(rt.partial_tree);
    }

    var termContentHtml = '';
    if (rt.stream_events && rt.stream_events.length > 0) {
      for (var si = 0; si < rt.stream_events.length; si++) {
        var evt = rt.stream_events[si];
        var timeTag = '<span class="stream-time">[' + (evt.elapsed || '?') + 's]</span> ';
        if (evt.type === 'text') {
          termContentHtml += '<div>' + timeTag + '<span class="stream-text">' + escHtml(evt.text || '') + '</span></div>';
        } else if (evt.type === 'tool_use') {
          termContentHtml += '<div>' + timeTag + '<span class="stream-tool">$ ' + escHtml(evt.tool || '?') + '</span> <span class="stream-text">' + escHtml(evt.input || '') + '</span></div>';
        } else if (evt.type === 'status') {
          termContentHtml += '<div>' + timeTag + '<span class="stream-status">' + escHtml(evt.text || '') + '</span></div>';
        } else {
          termContentHtml += '<div>' + timeTag + escHtml(evt.text || evt.type || '') + '</div>';
        }
      }
    }
    var termHtml = '<div class="stream-terminal-wrap">' +
      '<div class="stream-terminal-header" onclick="this.parentElement.classList.toggle(\'collapsed\')">' +
        '<span class="stream-terminal-toggle">&#9654;</span>' +
        '<span>CLI Output</span>' +
      '</div>' +
      '<div class="stream-terminal">' + termContentHtml + '</div>' +
    '</div>';

    // Build attempt nav footer if this is a retry
    var _attemptFooter = '';
    if (isRetryOfExisting && currentConvId && conversations[currentConvId]) {
      var _aMsgs = conversations[currentConvId].messages || [];
      var _prevTotal = 0;
      for (var _ai = _aMsgs.length - 1; _ai >= 0; _ai--) {
        if (_aMsgs[_ai].role === 'assistant' && _aMsgs[_ai].function === rt.func_name && _aMsgs[_ai].attempts) {
          _prevTotal = _aMsgs[_ai].attempts.length;
          break;
        }
      }
      if (_prevTotal > 0) {
        var _newTotal = _prevTotal + 1;
        _attemptFooter = '<div class="runtime-block-footer">' +
          '<div class="runtime-footer-left"></div>' +
          '<div class="runtime-footer-center">' +
            '<div class="attempt-nav">' +
              '<button class="attempt-nav-btn" disabled title="Previous attempt">&#9664;</button>' +
              '<span class="attempt-nav-label">' + _newTotal + '/' + _newTotal + '</span>' +
              '<button class="attempt-nav-btn" disabled title="Next attempt">&#9654;</button>' +
            '</div>' +
          '</div>' +
          '<div class="runtime-footer-right"></div>' +
        '</div>';
      }
    }

    var blockInnerHtml = hdr +
      '<div class="runtime-block-body"><div class="runtime-block-content">' +
        bodyContent + treeHtml + termHtml +
      '</div></div>' + _attemptFooter;

    if (isRetryOfExisting) {
      // Reuse the existing block instead of creating a new one
      existingBlock.className = 'runtime-block runtime-block-pending';
      existingBlock.id = 'runtime_pending';
      existingBlock.setAttribute('data-msg-id', rt.msg_id);
      existingBlock.innerHTML = blockInnerHtml;
    } else {
      var div = document.createElement('div');
      div.className = 'runtime-block runtime-block-pending';
      div.id = 'runtime_pending';
      div.setAttribute('data-msg-id', rt.msg_id);
      div.setAttribute('data-function', rt.func_name);
      div.innerHTML = blockInnerHtml;
      appendToChat(div);
    }

    var _termEl = (isRetryOfExisting ? existingBlock : div).querySelector('.stream-terminal');
    if (_termEl) _termEl.scrollTop = _termEl.scrollHeight;
    startElapsedTimer();
  }
}

// (toggleConvList, toggleFavList, doRefreshFunctions moved to sidebar.js)
function togglePanel() {}

// ===== Column Resize =====

(function() {
  function setupColResize(handleId, getTarget, setSide, minW) {
    var handle = document.getElementById(handleId);
    if (!handle) return;
    var startX, startW, target;

    handle.addEventListener('mousedown', function(e) {
      target = getTarget();
      if (!target) return;
      e.preventDefault();
      startX = e.clientX;
      startW = target.offsetWidth;
      handle.classList.add('dragging');
      target.style.transition = 'none';
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';

      function onMove(ev) {
        var dx = ev.clientX - startX;
        var newW = Math.max(minW, startW + dx * setSide);
        target.style.width = newW + 'px';
      }
      function onUp() {
        handle.classList.remove('dragging');
        target.style.transition = '';
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  setupColResize('sidebarResize', function() { return document.getElementById('sidebar'); }, 1, 180);
  setupColResize('detailResize', function() { return document.getElementById('detailPanel'); }, -1, 200);
})();

// (Panel resize removed — single conversations list now)

// (doRefreshFunctions moved to sidebar.js)

// ===== Event Listeners =====

// Thinking menu close-on-outside now handled by unified popover logic in ui.js

var chatInput = document.getElementById('chatInput');
chatInput.addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

document.addEventListener('keydown', function(e) {
  if (!_fnFormActive) return;
  var wrapper = document.querySelector('.input-wrapper');
  if (!wrapper || !wrapper.dataset.fnName) return;
  if (e.key === 'Escape') {
    closeFnForm();
    e.preventDefault();
  }
  if (e.key === 'Enter' && !e.shiftKey && e.target.classList.contains('fn-form-input') && e.target.tagName !== 'TEXTAREA') {
    e.preventDefault();
    submitFnForm(wrapper.dataset.fnName);
  }
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && e.target.classList.contains('fn-form-input')) {
    e.preventDefault();
    submitFnForm(wrapper.dataset.fnName);
  }
});

chatInput.addEventListener('input', function() {
  autoResize(chatInput);
});

// ===== Keepalive =====
setInterval(function() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send('ping');
  }
}, 30000);

// ===== Lifecycle =====
window.addEventListener('beforeunload', function() {
  var area = document.getElementById('chatArea');
  if (area) sessionStorage.setItem('agentic_scroll', area.scrollTop);
});

// ===== Init =====
connect();
loadProviders();
// Only show welcome on /new, not on /c/{id}
if (!window.location.pathname.match(/^\/c\//)) {
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
  } catch (_) {}
  if (typeof _updatePlusBtnIndicator === 'function') {
    _updatePlusBtnIndicator();
  }
})();

// Check for ?run= parameter from programs page
(function() {
  var params = new URLSearchParams(window.location.search);
  var runName = params.get('run');
  var runCat = params.get('cat');
  if (runName) {
    history.replaceState(null, '', '/chat');
    var waitForFunctions = setInterval(function() {
      if (availableFunctions.length > 0) {
        clearInterval(waitForFunctions);
        clickFunction(runName, runCat || 'user');
      }
    }, 200);
    setTimeout(function() { clearInterval(waitForFunctions); }, 5000);
  }
})();
