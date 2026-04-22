function renderConversations() {
  var container = document.getElementById('convList');
  var html = '';
  var convs = Object.values(conversations).sort(function(a, b) { return (b.created_at || 0) - (a.created_at || 0); });
  if (convs.length === 0) {
    html += '<div style="padding:8px 16px;font-size:12px;color:var(--text-muted)">No conversations yet</div>';
  } else {
    for (var ci = 0; ci < convs.length; ci++) {
      var c = convs[ci];
      var active = c.id === currentConvId ? ' active' : '';
      html += '<div class="conv-item' + active + '" onclick="switchConversation(\'' + c.id + '\')" title="' + escAttr(c.title || 'Untitled') + '">' +
        '<span class="conv-title">' + escHtml(c.title || 'Untitled') + '</span>' +
        '<span class="conv-del" onclick="event.stopPropagation();deleteConversation(\'' + c.id + '\')" title="Delete"><svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="2" y1="2" x2="8" y2="8"/><line x1="8" y1="2" x2="2" y2="8"/></svg></span>' +
      '</div>';
    }
    html += '<div class="conv-clear-all" onclick="clearAllConversations()">Clear all</div>';
  }
  container.innerHTML = html;
}

function switchConversation(convId) {
  // If already on this conversation, just reload in-place
  if (convId === currentConvId && window.location.pathname === '/c/' + convId) {
    return;
  }
  if (window.__navigate) { window.__navigate('/c/' + convId); return; }
  window.location.href = '/c/' + convId;
}

function _showConfirm(title, message, onConfirm) {
  var overlay = document.createElement('div');
  overlay.className = 'confirm-overlay';
  overlay.innerHTML =
    '<div class="confirm-dialog">' +
      '<div class="confirm-title">' + title + '</div>' +
      '<div class="confirm-message">' + message + '</div>' +
      '<div class="confirm-actions">' +
        '<button class="confirm-btn" id="_confirmCancel">Cancel</button>' +
        '<button class="confirm-btn confirm-btn-danger" id="_confirmOk">Delete</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(overlay);
  requestAnimationFrame(function() { overlay.classList.add('visible'); });

  function close() {
    overlay.classList.remove('visible');
    overlay.addEventListener('transitionend', function() { overlay.remove(); });
  }
  overlay.querySelector('#_confirmCancel').onclick = close;
  overlay.querySelector('#_confirmOk').onclick = function() { close(); onConfirm(); };
  overlay.addEventListener('click', function(e) { if (e.target === overlay) close(); });
}

function deleteConversation(convId) {
  var conv = conversations[convId];
  var title = (conv && conv.title) || 'Untitled';
  _showConfirm('Delete chat', 'Are you sure you want to delete "' + title + '"?', function() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'delete_conversation', conv_id: convId }));
    }
    delete conversations[convId];
    if (currentConvId === convId) {
      newConversation();
    }
    renderConversations();
  });
}

function clearAllConversations() {
  var count = Object.keys(conversations).length;
  if (!count) return;
  _showConfirm('Delete all chats', 'Are you sure you want to delete all ' + count + ' conversations? This cannot be undone.', function() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'clear_conversations' }));
    }
    conversations = {};
    newConversation();
    renderConversations();
  });
}

function newConversation() {
  if (window.location.pathname !== '/chat') {
    if (window.__navigate) { window.__navigate('/chat'); return; }
    window.location.href = '/chat';
    return;
  }
  // Already on /, reset in-place
  currentConvId = null;
  history.replaceState(null, '', '/chat');
  pendingResponses = {};
  trees = [];
  var container = document.getElementById('chatMessages');
  container.innerHTML = '';
  var welcome = document.createElement('div');
  welcome.className = 'welcome';
  welcome.id = 'welcomeScreen';
  welcome.innerHTML =
    '<div class="welcome-top">' +
      '<div class="welcome-logo">{<span class="logo-l1">L</span><span class="logo-l2">L</span><span class="logo-m">M</span><span class="welcome-logo-caret"></span>}</div>' +
      '<div class="welcome-title">Agentic Programming</div>' +
      '<div class="welcome-text">Run agentic functions, create new ones, or ask questions. Type a command or natural language below.</div>' +
    '</div>';
  container.appendChild(welcome);
  setWelcomeVisible(true);
  renderConversations();
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
  loadModelPills();
  loadAgentSettings();
}

function loadConversationData(data) {
  if (!data.messages) data.messages = [];
  conversations[data.id] = data;
  renderConversations();
  if (data.id === currentConvId) {
    var area = document.getElementById('chatArea');
    var hasSavedScroll = !!sessionStorage.getItem('agentic_scroll');
    if (hasSavedScroll) _skipScrollToBottom = true;
    renderConversationMessages(data);
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

function renderConversationMessages(conv) {
  var container = document.getElementById('chatMessages');
  trees = [];

  if (!conv.messages || conv.messages.length === 0) {
    container.innerHTML = '';
    var welcome = document.getElementById('welcomeScreen');
    if (welcome) {
      container.appendChild(welcome);
    }
    setWelcomeVisible(true);
    return;
  }

  setWelcomeVisible(false);
  container.innerHTML = '';

  for (var mi = 0; mi < conv.messages.length; mi++) {
    var msg = conv.messages[mi];
    if (msg.type === 'status') continue;

    if (msg.role === 'user' && msg.display === 'runtime') {
      var nextMsg = (mi + 1 < conv.messages.length) ? conv.messages[mi + 1] : null;
      if (nextMsg && nextMsg.role === 'assistant' && (nextMsg.display === 'runtime' || nextMsg.function)) {
        container.appendChild(_buildRestoredRuntimeBlock(msg, nextMsg, mi));
        mi++;
        continue;
      }
      container.appendChild(_buildInterruptedRuntimeBlock(msg));
      continue;
    }

    var div = document.createElement('div');
    if (msg.role === 'user') {
      div.className = 'message user';
      div.innerHTML =
        '<div class="message-header">' +
          '<div class="message-avatar user-avatar">U</div>' +
          '<div class="message-sender">You</div>' +
        '</div>' +
        '<div class="message-content">' + escHtml(msg.content || '') + '</div>';
    } else {
      var isOrphanRuntime = msg.display === 'runtime' || (msg.function && msg.function !== 'chat' && msg.type === 'result');
      if (isOrphanRuntime) {
        div = _buildOrphanRuntimeBlock(msg, mi);
      } else {
        div = _buildAssistantMessage(msg, mi);
      }
    }
    // Stamp msg_id so the hover action bar (copy/retry/branch) can
    // target the right server-side message. The retry endpoint
    // walks back to the nearest user turn, so passing an assistant
    // id (may be "{x}_reply") is equally valid.
    if (msg.id) div.setAttribute('data-msg-id', msg.id);
    // ContextGit metadata — sibling counts drive <N/M> nav, timestamp
    // drives the hover tooltip. See message-actions-nav.js.
    if (msg.sibling_index && msg.sibling_total) {
      div.setAttribute('data-sibling-index', String(msg.sibling_index));
      div.setAttribute('data-sibling-total', String(msg.sibling_total));
    }
    if (msg.timestamp || msg.created_at) {
      var ts = msg.timestamp || msg.created_at;
      div.setAttribute('data-created-at', String(ts));
      // Full date on hover (native browser tooltip). Server
      // timestamps may be seconds (time.time()) or ms — detect and
      // convert. Ignore bad inputs silently.
      try {
        var ms = ts > 1e12 ? ts : ts * 1000;
        div.title = new Date(ms).toLocaleString();
      } catch (e) { /* skip */ }
    }
    container.appendChild(div);
    if (typeof window.ensureMessageActions === 'function') {
      window.ensureMessageActions(div);
    }
  }

  // Expose the full message list to the nav module so it can walk
  // siblings without a round-trip. Populated here since this is the
  // only place we see the whole conversation at once.
  window._allMessages = conv.messages.slice();
  // Container-level run_active flag — CSS greys out Edit/Retry when
  // true. Flipped elsewhere when runs start / end; set it from the
  // snapshot we just loaded so initial state is right.
  var chatContainer = document.getElementById('chatMessages');
  if (chatContainer) {
    chatContainer.setAttribute(
      'data-run-active', conv.run_active ? 'true' : 'false',
    );
  }

  if (!_skipScrollToBottom) scrollToBottom();
  _skipScrollToBottom = false;
}

// --- Conversation message builders ---

function _getDisplayContent(msg) {
  var displayContent = msg.content || '';
  var displayTree = msg.context_tree || null;
  if (msg.attempts && msg.attempts.length > 0) {
    var aidx = msg.current_attempt || 0;
    if (aidx >= 0 && aidx < msg.attempts.length) {
      displayContent = msg.attempts[aidx].content || displayContent;
      displayTree = msg.attempts[aidx].tree || displayTree;
    }
  }
  return { content: displayContent, tree: displayTree };
}

function _buildRestoredRuntimeBlock(userMsg, assistantMsg, mi) {
  var parsed = parseRunCommandForDisplay(userMsg.content || '');
  var display = _getDisplayContent(assistantMsg);

  var resultContentHtml = renderMd(display.content);
  var treeHtml = '';
  var attemptNavHtml = '';
  var isError = assistantMsg.type === 'error';
  var _retryFn = assistantMsg.function || parsed.funcName;
  var rerunHtml = _retryFn ? '<button class="rerun-btn" onclick="retryCurrentBlock(\'' + escAttr(_retryFn) + '\')">&#8634; Retry</button>' : '';

  if (display.tree) {
    var inlineId = 'itree_restore_' + mi + '_' + (assistantMsg.function || 'result').replace(/[^a-zA-Z0-9]/g, '_');
    treeHtml = renderInlineTree(display.tree, inlineId);
    updateTreeData(display.tree);
  } else if (assistantMsg.context_tree) {
    var inlineId2 = 'itree_restore_' + mi + '_ctx';
    treeHtml = renderInlineTree(assistantMsg.context_tree, inlineId2);
    updateTreeData(assistantMsg.context_tree);
  }

  if (assistantMsg.attempts && assistantMsg.attempts.length > 1) {
    attemptNavHtml = renderAttemptNav(assistantMsg.function, assistantMsg.current_attempt || 0, assistantMsg.attempts.length);
  }

  var blockDiv = document.createElement('div');
  blockDiv.className = 'runtime-block' + (isError ? ' error' : '');
  if (assistantMsg.function) blockDiv.setAttribute('data-function', assistantMsg.function);
  var _usage = assistantMsg.usage || null;
  if (!_usage && assistantMsg.attempts && assistantMsg.attempts.length > 0) {
    var _curAttempt = assistantMsg.attempts[assistantMsg.current_attempt || 0];
    _usage = _curAttempt && _curAttempt.usage || null;
  }
  blockDiv.innerHTML = buildRuntimeBlockHtml(assistantMsg.function || parsed.funcName, parsed.params, resultContentHtml, treeHtml, attemptNavHtml, rerunHtml, _usage);
  return blockDiv;
}

function _buildInterruptedRuntimeBlock(msg) {
  var parsed = parseRunCommandForDisplay(msg.content || '');
  var div = document.createElement('div');
  div.className = 'runtime-block interrupted';
  div.setAttribute('data-function', parsed.funcName);
  var rerunHtml = '<button class="rerun-btn" onclick="retryCurrentBlock(\'' + escAttr(parsed.funcName) + '\')">&#8634; Retry</button>';
  div.innerHTML = buildRuntimeBlockHtml(
    parsed.funcName, parsed.params,
    '<span style="color:var(--text-muted)">Execution interrupted</span>',
    '', '', rerunHtml
  );
  return div;
}

function _buildOrphanRuntimeBlock(msg, mi) {
  var display = _getDisplayContent(msg);
  var resultContentHtml = renderMd(display.content);
  var treeHtml = '';
  var attemptNavHtml = '';
  var isError = msg.type === 'error';
  var rerunHtml = msg.function ? '<button class="rerun-btn" onclick="retryCurrentBlock(\'' + escAttr(msg.function) + '\')">&#8634; Retry</button>' : '';

  if (display.tree) {
    var inlineId = 'itree_restore_' + mi + '_' + (msg.function || 'result').replace(/[^a-zA-Z0-9]/g, '_');
    treeHtml = renderInlineTree(display.tree, inlineId);
    updateTreeData(display.tree);
  } else if (msg.context_tree) {
    var inlineId2 = 'itree_restore_' + mi + '_ctx';
    treeHtml = renderInlineTree(msg.context_tree, inlineId2);
    updateTreeData(msg.context_tree);
  }
  if (msg.attempts && msg.attempts.length > 1) {
    attemptNavHtml = renderAttemptNav(msg.function, msg.current_attempt || 0, msg.attempts.length);
  }

  var div = document.createElement('div');
  div.className = 'runtime-block' + (isError ? ' error' : '');
  if (msg.function) div.setAttribute('data-function', msg.function);
  var _usage = msg.usage || null;
  if (!_usage && msg.attempts && msg.attempts.length > 0) {
    var _curAttempt = msg.attempts[msg.current_attempt || 0];
    _usage = _curAttempt && _curAttempt.usage || null;
  }
  div.innerHTML = buildRuntimeBlockHtml(msg.function || '', '', resultContentHtml, treeHtml, attemptNavHtml, rerunHtml, _usage);
  return div;
}

function _buildAssistantMessage(msg, mi) {
  var div = document.createElement('div');
  div.className = 'message assistant';
  if (msg.function) div.setAttribute('data-function', msg.function);

  var cHtml = '';
  // Plain chat with persisted thinking/tool blocks — rebuild the same
  // collapsible scaffold the live stream produced.
  var hasBlocks = !msg.function && msg.blocks && msg.blocks.length;
  if (hasBlocks && typeof _renderAssistantBlocks === 'function') {
    cHtml = _renderAssistantBlocks(msg.blocks, msg.content || '');
    div.innerHTML =
      '<div class="message-header">' +
        '<div class="message-avatar bot-avatar">A</div>' +
        '<div class="message-sender">Agentic</div>' +
      '</div>' + cHtml;
    return div;
  }
  if (msg.type === 'error') {
    cHtml = '<div class="error-content">' + escHtml(msg.content || '') + '</div>';
  } else if (msg.type === 'result') {
    var display = _getDisplayContent(msg);
    cHtml = '<div class="message-content">';
    if (msg.function) {
      cHtml += '<div style="margin-bottom:4px"><span style="font-family:var(--font-mono);color:var(--accent-green);font-size:12px">' +
        escHtml(msg.function) + '()</span> completed</div>';
    }
    cHtml += renderMd(display.content) + '</div>';

    if (msg.attempts && msg.attempts.length > 1) {
      cHtml += renderAttemptNav(msg.function, msg.current_attempt || 0, msg.attempts.length);
    }

    if (display.tree) {
      var inlineId = 'itree_restore_' + mi + '_' + (msg.function || 'result').replace(/[^a-zA-Z0-9]/g, '_');
      cHtml += renderInlineTree(display.tree, inlineId);
      updateTreeData(display.tree);
    } else if (msg.context_tree) {
      var inlineId2 = 'itree_restore_' + mi + '_ctx';
      cHtml += renderInlineTree(msg.context_tree, inlineId2);
      updateTreeData(msg.context_tree);
    }
  } else {
    cHtml = '<div class="message-content">' + renderMd(msg.content || '') + '</div>';
  }

  div.innerHTML =
    '<div class="message-header">' +
      '<div class="message-avatar bot-avatar">A</div>' +
      '<div class="message-sender">Agentic</div>' +
    '</div>' +
    cHtml;
  return div;
}

function handleAttemptSwitched(data) {
  if (data.tree && (data.tree.path || data.tree.name)) {
    var rootKey = data.tree.path || data.tree.name;
    var idx = trees.findIndex(function(t) { return t.path === rootKey || t.name === data.tree.name; });
    if (idx >= 0) { trees[idx] = data.tree; } else { trees.push(data.tree); }
  }

  if (currentConvId && conversations[currentConvId]) {
    var conv = conversations[currentConvId];
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
    renderConversationMessages(conv);
    var el = document.querySelector('[data-function="' + data.function + '"]');
    if (el) {
      requestAnimationFrame(function() { el.scrollIntoView({ block: 'center' }); });
    }
  }
}

// ===== Functions Panel =====

