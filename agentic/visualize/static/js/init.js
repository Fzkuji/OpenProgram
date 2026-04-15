// ===== WebSocket Connection =====

function connect() {
  var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws');

  ws.onopen = function() {
    updateStatus('connected');
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    loadAgentSettings();
    ws.send(JSON.stringify({ action: 'list_conversations' }));
    // Read conv_id from URL path: /c/{conv_id} — only load on /c/ pages, not /new
    var pathMatch = window.location.pathname.match(/^\/c\/([^/]+)/);
    if (pathMatch) {
      var convId = pathMatch[1];
      currentConvId = convId;
      localStorage.setItem('agentic_conv_id', convId);
      ws.send(JSON.stringify({ action: 'load_conversation', conv_id: convId }));
    } else {
      // On /new — clear stored conv_id
      currentConvId = null;
      localStorage.removeItem('agentic_conv_id');
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
        localStorage.setItem('agentic_conv_id', currentConvId);
        // Update URL to /c/{conv_id} without full page reload
        if (window.location.pathname !== '/c/' + currentConvId) {
          history.pushState(null, '', '/c/' + currentConvId);
        }
        if (!conversations[currentConvId]) {
          conversations[currentConvId] = { id: currentConvId, title: 'New conversation', messages: [] };
        }
        renderConversations();
      }
      break;
    case 'chat_response':
      handleChatResponse(msg.data);
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
    case 'status':
      isPaused = msg.paused;
      updatePauseBtn();
      refreshInlineTrees();
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
        conversations[c.id] = { id: c.id, title: c.title, messages: [], created_at: c.created_at, has_session: c.has_session };
      } else {
        conversations[c.id].has_session = c.has_session;
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

// ===== Section collapse =====
function toggleConvList() {
  var list = document.getElementById('convList');
  var hint = document.getElementById('convHint');
  if (!list) return;
  var hidden = list.style.display === 'none';
  list.style.display = hidden ? '' : 'none';
  if (hint) hint.textContent = hidden ? 'Hide' : 'Show';
}

function toggleFavList() {
  var list = document.getElementById('favList');
  var hint = document.getElementById('favHint');
  if (!list) return;
  var hidden = list.style.display === 'none';
  list.style.display = hidden ? '' : 'none';
  if (hint) hint.textContent = hidden ? 'Hide' : 'Show';
}
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

// ===== Refresh Button with Spin Feedback =====

function doRefreshFunctions(btn) {
  if (btn.classList.contains('spinning')) return;
  var svg = btn.querySelector('svg');
  if (!svg) return;
  btn.classList.add('spinning');
  refreshFunctions();
  svg.addEventListener('animationend', function handler() {
    svg.removeEventListener('animationend', handler);
    btn.classList.remove('spinning');
    // Show green checkmark briefly
    var orig = btn.innerHTML;
    btn.innerHTML = '&#10003;';
    btn.classList.add('done');
    setTimeout(function() {
      btn.innerHTML = orig;
      btn.classList.remove('done');
    }, 800);
  });
}

// ===== Event Listeners =====

document.addEventListener('click', function(e) {
  var menu = document.getElementById('thinkingMenu');
  if (menu && !e.target.closest('.input-options')) {
    menu.classList.remove('open');
    var sel = document.getElementById('thinkingSelector');
    if (sel) sel.classList.remove('open');
  }
});

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

// Check for ?run= parameter from programs page
(function() {
  var params = new URLSearchParams(window.location.search);
  var runName = params.get('run');
  var runCat = params.get('cat');
  if (runName) {
    history.replaceState(null, '', '/new');
    var waitForFunctions = setInterval(function() {
      if (availableFunctions.length > 0) {
        clearInterval(waitForFunctions);
        clickFunction(runName, runCat || 'user');
      }
    }, 200);
    setTimeout(function() { clearInterval(waitForFunctions); }, 5000);
  }
})();
