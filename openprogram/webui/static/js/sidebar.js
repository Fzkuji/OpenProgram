// ===== Sidebar: Conversations, Functions, Forms =====

function toggleSidebar() {
  var sb = document.getElementById('sidebar');
  sidebarOpen = !sidebarOpen;
  sb.style.removeProperty('width');
  sb.classList.toggle('collapsed', !sidebarOpen);
}

// ===== Conversations =====

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
  if (window.location.pathname !== '/new') {
    window.location.href = '/new';
    return;
  }
  // Already on /, reset in-place
  currentConvId = null;
  history.replaceState(null, '', '/new');
  pendingResponses = {};
  trees = [];
  var container = document.getElementById('chatMessages');
  container.innerHTML = '';
  var welcome = document.createElement('div');
  welcome.className = 'welcome';
  welcome.id = 'welcomeScreen';
  welcome.innerHTML =
    '<div class="welcome-top">' +
      '<div class="welcome-logo">{<span class="logo-l1">L</span><span class="logo-l2">L</span><span class="logo-m">M</span>}</div>' +
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
    container.appendChild(div);
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

async function loadProgramsMeta() {
  try {
    var resp = await fetch('/api/programs/meta');
    var data = await resp.json();
    programsMeta = data || { favorites: [], folders: {} };
  } catch(e) {
    programsMeta = { favorites: [], folders: {} };
  }
}

function renderFunctions() {
  var container = document.getElementById('favList');
  var section = document.getElementById('favSection');
  if (!container || !section) return;

  var favSet = new Set(programsMeta.favorites || []);
  var favFiltered = availableFunctions.filter(function(f) { return favSet.has(f.name); });
  var catOrder = ['app', 'generated', 'user', 'meta', 'builtin'];
  var favFns = [];
  for (var ci = 0; ci < catOrder.length; ci++) {
    for (var fi = 0; fi < favFiltered.length; fi++) {
      if ((favFiltered[fi].category || 'user') === catOrder[ci]) favFns.push(favFiltered[fi]);
    }
  }

  if (favFns.length === 0) {
    section.classList.add('empty');
    container.innerHTML = '';
    return;
  }

  section.classList.remove('empty');
  var catIcons = { app: '\u{1F4E6}', meta: '\u{1F6E0}', builtin: '\u2699', generated: '\u2699', user: '\u270E' };
  var maxShow = 4;
  var html = '';
  for (var i = 0; i < Math.min(favFns.length, maxShow); i++) {
    var f = favFns[i];
    var cat = f.category || 'user';
    var icon = catIcons[cat] || '\u270E';
    html += '<div class="fav-item" onclick="clickFunction(\'' + escAttr(f.name) + '\', \'' + escAttr(cat) + '\')" title="' + escAttr(f.description || '') + '">' +
      '<span class="fav-icon">' + icon + '</span>' +
      '<span class="fav-name">' + escHtml(f.name) + '</span>' +
    '</div>';
  }
  container.innerHTML = html;
}

async function refreshFunctions() {
  try {
    var resp = await fetch('/api/functions');
    availableFunctions = await resp.json();
    renderFunctions();
  } catch(e) { console.error('Refresh failed:', e); }
}

async function deleteFunction(name) {
  if (!confirm('Delete function "' + name + '"?')) return;
  try {
    var resp = await fetch('/api/function/' + encodeURIComponent(name), { method: 'DELETE' });
    var data = await resp.json();
    if (data.deleted) {
      addAssistantMessage('Deleted function "' + name + '".');
      var fResp = await fetch('/api/functions');
      availableFunctions = await fResp.json();
      renderFunctions();
    } else {
      addAssistantMessage('Cannot delete: ' + (data.error || 'unknown error'));
    }
  } catch(e) { alert('Delete failed: ' + e.message); }
}

async function fixFunction(name) {
  var instruction = prompt('What should be fixed in ' + name + '?');
  if (!instruction) return;
  var input = document.getElementById('chatInput');
  input.value = 'fix ' + name + ' ' + instruction;
  sendMessage();
}

// ===== Function Form =====

function clickFunction(name, category) {
  var fn = availableFunctions.find(function(f) { return f.name === name; });
  if (fn) showFnForm(fn);
}

function clickFnExample(fnName) {
  var fn = availableFunctions.find(function(f) { return f.name === fnName; });
  if (fn) {
    showFnForm(fn);
  } else {
    setInput('run ' + fnName + ' ');
  }
}

function setInput(text) {
  if (_fnFormActive) closeFnForm();
  var input = document.getElementById('chatInput');
  input.value = text;
  input.focus();
  autoResize(input);
}

function _buildFieldsHtml(fn) {
  var params = (fn.params_detail || []).filter(function(p) {
    if (p.name === 'runtime' || p.name === 'callback' || p.name === 'exec_runtime' || p.name === 'review_runtime') return false;
    if (p.hidden) return false;
    return true;
  });

  var fieldsHtml = '';
  for (var i = 0; i < params.length; i++) {
    var p = params[i];
    var typeLabel = p.type ? '<span class="fn-form-label-type">' + escHtml(p.type) + '</span>' : '';
    var reqLabel = p.required
      ? '<span class="fn-form-label-required">*</span>'
      : '<span class="fn-form-label-optional">optional</span>';
    var descSpan = p.description
      ? '<span class="fn-form-label-desc">' + escHtml(p.description) + '</span>'
      : '';

    var placeholder = p.placeholder || '';
    if (!placeholder && p.default && p.default !== 'None' && !(p.default + '').startsWith('_')) {
      placeholder = 'default: ' + p.default;
    }

    var isBool = p.type === 'bool' || p.type === 'boolean';
    var isMultiline = p.multiline !== undefined ? p.multiline : (!isBool && (p.type === 'str' || p.type === 'string' || !p.type));
    var inputTag;
    var defaultVal = (p.default || '').replace(/^["']|["']$/g, '');

    if (isBool) {
      var yesActive = (defaultVal === 'True') ? ' active' : '';
      var noActive = (defaultVal === 'False' || !defaultVal) ? ' active' : '';
      inputTag =
        '<div class="fn-form-toggle" id="fnField_' + escAttr(p.name) + '" data-value="' + (defaultVal === 'True' ? 'True' : 'False') + '">' +
          '<button type="button" class="fn-form-toggle-btn' + yesActive + '" onclick="toggleBool(\'' + escAttr(p.name) + '\', \'True\', this)">Yes</button>' +
          '<button type="button" class="fn-form-toggle-btn' + noActive + '" onclick="toggleBool(\'' + escAttr(p.name) + '\', \'False\', this)">No</button>' +
        '</div>';
    } else if (p.options_from === 'functions') {
      var fnOpts = availableFunctions.filter(function(f) {
        var cat = f.category || 'user';
        return cat !== 'meta' && cat !== 'builtin';
      });
      var selectHtml = '<option value="">-- select --</option>';
      for (var j = 0; j < fnOpts.length; j++) {
        selectHtml += '<option value="' + escAttr(fnOpts[j].name) + '">' + escHtml(fnOpts[j].name) + '</option>';
      }
      inputTag = '<select class="fn-form-input fn-form-select" id="fnField_' + escAttr(p.name) + '">' + selectHtml + '</select>';
    } else if (p.options && p.options.length > 0) {
      var chipsHtml = '';
      for (var j = 0; j < p.options.length; j++) {
        var isDefault = (p.options[j] === defaultVal) ? ' active' : '';
        chipsHtml += '<button type="button" class="fn-form-option-chip' + isDefault + '" onclick="selectOption(\'' + escAttr(p.name) + '\', \'' + escAttr(p.options[j]) + '\', this)">' + escHtml(p.options[j]) + '</button>';
      }
      chipsHtml += '<input type="text" class="fn-form-option-custom" placeholder="..." ' +
        'oninput="selectOptionCustom(\'' + escAttr(p.name) + '\', this)">';
      inputTag = '<div class="fn-form-options" id="fnField_' + escAttr(p.name) + '" data-value="' + escAttr(defaultVal) + '">' + chipsHtml + '</div>';
    } else if (isMultiline) {
      inputTag = '<textarea class="fn-form-input fn-form-textarea" id="fnField_' + escAttr(p.name) + '" placeholder="' + escAttr(placeholder) + '" rows="2"></textarea>';
    } else {
      inputTag = '<input class="fn-form-input" id="fnField_' + escAttr(p.name) + '" placeholder="' + escAttr(placeholder) + '">';
    }

    fieldsHtml +=
      '<div class="fn-form-field">' +
        '<div class="fn-form-label">' +
          '<span class="fn-form-label-name">' + escHtml(p.name) + '</span>' +
          typeLabel + reqLabel + descSpan +
        '</div>' +
        inputTag +
      '</div>';
  }

  if (params.length === 0) {
    fieldsHtml = '<div class="fn-form-no-params">No parameters needed — click run to execute</div>';
  }
  return fieldsHtml;
}


// Pin bottom-row to its current screen position during height animations
function _pinBottomRow(bottomRow) {
  if (!bottomRow) return function(){};
  var rect = bottomRow.getBoundingClientRect();
  bottomRow.style.position = 'fixed';
  bottomRow.style.left = rect.left + 'px';
  bottomRow.style.top = rect.top + 'px';
  bottomRow.style.width = rect.width + 'px';
  bottomRow.style.bottom = 'auto';
  bottomRow.style.right = 'auto';
  return function() {
    bottomRow.style.position = '';
    bottomRow.style.left = '';
    bottomRow.style.top = '';
    bottomRow.style.width = '';
    bottomRow.style.bottom = '';
    bottomRow.style.right = '';
  };
}

/**
 * Pin send button at its current visual position (position:fixed) so it
 * stays put while the wrapper height animates. Returns an unpin function
 * that uses FLIP to smoothly animate the button to its CSS-determined
 * final position.
 */
/* Send button animation handled purely by CSS transition on `bottom`.
   Wrapper bottom edge is fixed, so bottom-based positioning is stable. */

function _buildFormHtml(fn, fieldsHtml) {
  // No footer — .input-bottom-row stays as permanent element in wrapper
  var workdirHtml = (typeof buildWorkdirField === 'function') ? buildWorkdirField() : '';
  return '<div class="fn-form-header">' +
    '<div class="fn-form-title">' +
      '<span class="fn-form-name"><span style="color:var(--text-secondary);font-weight:400">function </span>' + escHtml(fn.name) + '</span>' +
      '<span class="fn-form-desc">' + escHtml(fn.description || '') + '</span>' +
    '</div>' +
    '<button class="fn-form-close" onclick="closeFnForm()" title="Close">&times;</button>' +
  '</div>' +
  '<div class="fn-form-body">' + workdirHtml + fieldsHtml + '</div>';
}

function _showFnFormSwitch(fn, wrapper, sendBtn) {
  var heightBefore = wrapper.offsetHeight;

  // Build new content HTML first
  var fieldsHtml = _buildFieldsHtml(fn);
  var formHtml = _buildFormHtml(fn, fieldsHtml);

  // Measure target height with a hidden clone
  var measure = wrapper.cloneNode(false);
  measure.style.cssText = 'position:absolute;visibility:hidden;pointer-events:none;width:' + wrapper.offsetWidth + 'px;height:auto;overflow:visible;';
  var sendClone = document.getElementById('sendBtn').cloneNode(true);
  var stopClone = document.getElementById('stopBtn').cloneNode(true);
  var bottomRow = wrapper.querySelector('.input-bottom-row');
  measure.appendChild(sendClone);
  measure.appendChild(stopClone);
  measure.insertAdjacentHTML('beforeend', formHtml);
  if (bottomRow) measure.appendChild(bottomRow.cloneNode(true));
  wrapper.parentNode.appendChild(measure);
  var heightAfter = measure.offsetHeight;
  measure.remove();

  // Lock current height, pin bottom-row and send button
  wrapper.style.height = heightBefore + 'px';
  wrapper.style.overflow = 'hidden';
  wrapper.style.transition = 'none';
  var unpinBottomRow = _pinBottomRow(bottomRow);


  // Swap content (insert before bottomRow)
  var oldParts = wrapper.querySelectorAll('.fn-form-header, .fn-form-body');
  for (var i = 0; i < oldParts.length; i++) oldParts[i].remove();
  var temp = document.createElement('div');
  temp.innerHTML = formHtml;
  while (temp.firstChild) wrapper.insertBefore(temp.firstChild, bottomRow);

  wrapper.dataset.fnName = fn.name;
  sendBtn.setAttribute('onclick', "submitFnForm('" + escAttr(fn.name) + "')");
  if (typeof buildThinkingMenu === 'function') buildThinkingMenu();
  if (typeof initWorkdirField === 'function') initWorkdirField(fn.name);

  // Animate to target height
  requestAnimationFrame(function() {
    wrapper.style.transition = 'height 0.25s cubic-bezier(0.25, 0.1, 0.25, 1)';
    wrapper.style.height = heightAfter + 'px';
    wrapper.addEventListener('transitionend', function handler(e) {
      if (e.target !== wrapper || e.propertyName !== 'height') return;
      wrapper.style.height = '';
      wrapper.style.overflow = '';
      wrapper.style.transition = '';
      unpinBottomRow();

      wrapper.removeEventListener('transitionend', handler);
    });
  });

  // Setup textarea auto-resize
  setTimeout(function() {
    var textareas = wrapper.querySelectorAll('.fn-form-textarea');
    for (var i = 0; i < textareas.length; i++) {
      textareas[i].addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 160) + 'px';
      });
    }
  }, 50);
}

function showFnForm(fn) {
  var wrapper = document.querySelector('.input-wrapper');
  if (!wrapper) return;

  // Save only the swappable content (sendBtn, stopBtn, input-bottom-row stay in wrapper)
  var sendBtn = document.getElementById('sendBtn');
  var stopBtn = document.getElementById('stopBtn');
  var bottomRow = wrapper.querySelector('.input-bottom-row');
  if (!_fnFormActive) {
    // Save children except permanent elements
    _inputContentOriginal = [];
    var children = wrapper.children;
    for (var i = 0; i < children.length; i++) {
      if (children[i] !== sendBtn && children[i] !== stopBtn && children[i] !== bottomRow) {
        _inputContentOriginal.push(children[i]);
      }
    }
  } else {
    // Already have a form open — switch content in-place
    _showFnFormSwitch(fn, wrapper, sendBtn);
    return;
  }
  _fnFormActive = true;

  // --- Hide welcome examples with height collapse ---
  var examples = document.getElementById('welcomeExamples');
  if (examples) {
    var exH = examples.offsetHeight;
    examples.style.height = exH + 'px';
    examples.style.overflow = 'hidden';
    examples.style.opacity = '1';
    examples.style.pointerEvents = 'none';
    requestAnimationFrame(function() {
      examples.style.transition = 'opacity 0.15s ease, height 0.25s cubic-bezier(0.25, 0.1, 0.25, 1)';
      examples.style.opacity = '0';
      examples.style.height = '0px';
      examples.style.padding = '0 24px';
    });
  }

  // --- Capture before state ---
  var wrapperBefore = wrapper.getBoundingClientRect();

  // --- Build form HTML ---
  var fieldsHtml = _buildFieldsHtml(fn);

  // --- Replace content (keep sendBtn, stopBtn, bottomRow) ---
  wrapper.style.height = wrapperBefore.height + 'px';
  wrapper.style.overflow = 'hidden';

  // Remove old content (not permanent elements)
  _inputContentOriginal.forEach(function(el) { el.remove(); });

  // Build form content as DOM (inserted before bottomRow which stays)
  var formHtml = _buildFormHtml(fn, fieldsHtml);
  var temp = document.createElement('div');
  temp.innerHTML = formHtml;
  while (temp.firstChild) wrapper.insertBefore(temp.firstChild, bottomRow);

  // --- Freeze send button + context stats before class change ---
  var sendBtnBottom = parseFloat(getComputedStyle(sendBtn).bottom);
  sendBtn.style.transition = 'none';
  sendBtn.style.bottom = sendBtnBottom + 'px';
  var ctxStats = wrapper.querySelector('.context-stats-label');
  if (ctxStats) { ctxStats.style.transition = 'none'; ctxStats.style.marginRight = '0'; }
  void sendBtn.offsetHeight;

  wrapper.className = 'input-wrapper fn-form-mode';
  wrapper.dataset.fnName = fn.name;
  sendBtn.setAttribute('onclick', "submitFnForm('" + escAttr(fn.name) + "')");
  sendBtn.title = 'Run';
  if (typeof buildThinkingMenu === 'function') buildThinkingMenu();

  // --- Set initial opacity for fade-in ---
  var formHeader = wrapper.querySelector('.fn-form-header');
  var formBody = wrapper.querySelector('.fn-form-body');
  if (formHeader) formHeader.style.opacity = '0';
  if (formBody) formBody.style.opacity = '0';
  // Bottom separator starts transparent, fades in with header
  if (bottomRow) { bottomRow.style.transition = 'none'; bottomRow.style.borderTopColor = 'transparent'; void bottomRow.offsetHeight; }

  // --- Measure target height ---
  var wrapperAfterHeight = wrapper.scrollHeight;

  // --- Pin bottom-row so it doesn't move during animation ---
  var unpinBottomRow = _pinBottomRow(bottomRow);

  // --- Release send button + context stats: animate simultaneously ---
  sendBtn.style.transition = '';
  sendBtn.style.bottom = '';
  if (ctxStats) { ctxStats.style.transition = ''; ctxStats.style.marginRight = ''; }

  // --- Single rAF: animate height + fade in content ---
  requestAnimationFrame(function() {
    wrapper.style.transition = 'height 0.3s cubic-bezier(0.25, 0.1, 0.25, 1)';
    wrapper.style.height = wrapperAfterHeight + 'px';

    if (formHeader) { formHeader.style.transition = 'opacity 0.25s ease 0.1s'; formHeader.style.opacity = '1'; }
    if (formBody) { formBody.style.transition = 'opacity 0.25s ease 0.15s'; formBody.style.opacity = '1'; }
    if (bottomRow) { bottomRow.style.transition = 'border-color 0.25s ease 0.1s'; bottomRow.style.borderTopColor = ''; }

    wrapper.addEventListener('transitionend', function handler(e) {
      if (e.target !== wrapper || e.propertyName !== 'height') return;
      wrapper.style.height = '';
      wrapper.style.overflow = '';
      wrapper.style.transition = '';
      unpinBottomRow();

      wrapper.removeEventListener('transitionend', handler);
    });
  });

  // --- Setup textarea auto-resize ---
  setTimeout(function() {
    var textareas = wrapper.querySelectorAll('.fn-form-textarea');
    for (var i = 0; i < textareas.length; i++) {
      textareas[i].addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 160) + 'px';
      });
    }
  }, 50);

  // --- Prefill workdir from server (remembered per conversation+function) ---
  if (typeof initWorkdirField === 'function') initWorkdirField(fn.name);
}

function closeFnForm() {
  if (!_fnFormActive) return;
  var wrapper = document.querySelector('.input-wrapper');
  if (!wrapper) return;
  var sendBtn = document.getElementById('sendBtn');
  var stopBtn = document.getElementById('stopBtn');

  // Measure target height (include permanent bottomRow)
  var bottomRow = wrapper.querySelector('.input-bottom-row');
  var measure = wrapper.cloneNode(false);
  measure.className = 'input-wrapper';
  _inputContentOriginal.forEach(function(el) { measure.appendChild(el.cloneNode(true)); });
  if (bottomRow) measure.appendChild(bottomRow.cloneNode(true));
  measure.style.cssText = 'position:absolute;visibility:hidden;pointer-events:none;width:' + wrapper.offsetWidth + 'px';
  wrapper.parentNode.appendChild(measure);
  var targetHeight = measure.offsetHeight;
  wrapper.parentNode.removeChild(measure);

  // Step 1: Fade out form content + bottom separator
  var formParts = wrapper.querySelectorAll('.fn-form-header, .fn-form-body');
  formParts.forEach(function(el) {
    el.style.transition = 'opacity 0.12s ease';
    el.style.opacity = '0';
  });
  if (bottomRow) {
    bottomRow.style.transition = 'border-color 0.12s ease';
    bottomRow.style.borderTopColor = 'transparent';
  }

  // Step 2: Lock height, pin bottom-row, then shrink
  var heightBefore = wrapper.offsetHeight;
  wrapper.style.height = heightBefore + 'px';
  wrapper.style.overflow = 'hidden';
  var unpinBottomRow = _pinBottomRow(bottomRow);

  // Start send button + context stats moving simultaneously with height shrink.
  var sendBtnTargetBottom = targetHeight - 42;
  sendBtn.style.bottom = sendBtnTargetBottom + 'px';
  var ctxStats = wrapper.querySelector('.context-stats-label');
  if (ctxStats) ctxStats.style.marginRight = '0';

  requestAnimationFrame(function() {
    wrapper.style.transition = 'height 0.3s cubic-bezier(0.25, 0.1, 0.25, 1)';
    wrapper.style.height = targetHeight + 'px';

    // Show welcome examples
    var examples = document.getElementById('welcomeExamples');
    if (examples) {
      examples.style.transition = 'none';
      examples.style.height = '';
      examples.style.padding = '';
      examples.style.overflow = 'hidden';
      examples.style.opacity = '0';
      examples.style.pointerEvents = '';
      var naturalH = examples.scrollHeight;
      examples.style.height = '0px';
      examples.style.padding = '0 24px';
      requestAnimationFrame(function() {
        examples.style.transition = 'opacity 0.2s ease 0.1s, height 0.3s cubic-bezier(0.25, 0.1, 0.25, 1), padding 0.3s ease';
        examples.style.opacity = '1';
        examples.style.height = naturalH + 'px';
        examples.style.padding = '';
        examples.addEventListener('transitionend', function handler(e) {
          if (e.propertyName !== 'height') return;
          examples.style.height = '';
          examples.style.overflow = '';
          examples.style.transition = '';
          examples.removeEventListener('transitionend', handler);
        });
      });
    }

    // Step 3: After shrink, swap content (keep buttons)
    wrapper.addEventListener('transitionend', function handler(e) {
      if (e.target !== wrapper || e.propertyName !== 'height') return;
      wrapper.removeEventListener('transitionend', handler);

      wrapper.style.height = '';
      wrapper.style.overflow = '';
      wrapper.style.transition = '';
      unpinBottomRow();



      // Remove form content (not permanent elements)
      var toRemove = wrapper.querySelectorAll('.fn-form-header, .fn-form-body');
      toRemove.forEach(function(el) { el.remove(); });

      // Restore original content (before bottomRow)
      var br = wrapper.querySelector('.input-bottom-row');
      _inputContentOriginal.forEach(function(el) { wrapper.insertBefore(el, br); });

      wrapper.className = 'input-wrapper';
      _fnFormActive = false;
      delete wrapper.dataset.fnName;
      // Clear inline overrides from close animation
      var br2 = wrapper.querySelector('.input-bottom-row');
      if (br2) { br2.style.borderTopColor = ''; br2.style.transition = ''; }
      var ctx2 = wrapper.querySelector('.context-stats-label');
      if (ctx2) ctx2.style.marginRight = '';

      // Restore send button for chat mode
      sendBtn.style.bottom = ''; // clear inline, let CSS take over
      sendBtn.setAttribute('onclick', 'onSendBtnClick()');
      sendBtn.title = 'Send message';

      // Re-bind
      var chatInput = document.getElementById('chatInput');
      if (chatInput) {
        chatInput.addEventListener('keydown', function(e) {
          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
        });
        chatInput.addEventListener('input', function() { autoResize(chatInput); });
      }
      if (typeof buildThinkingMenu === 'function') buildThinkingMenu();
    });
  });
}

function toggleBool(paramName, value, btnEl) {
  var container = document.getElementById('fnField_' + paramName);
  if (!container) return;
  container.dataset.value = value;
  var btns = container.querySelectorAll('.fn-form-toggle-btn');
  for (var i = 0; i < btns.length; i++) btns[i].classList.remove('active');
  btnEl.classList.add('active');
}

function selectOption(paramName, value, chipEl) {
  var container = document.getElementById('fnField_' + paramName);
  if (!container) return;
  container.dataset.value = value;
  var chips = container.querySelectorAll('.fn-form-option-chip');
  for (var i = 0; i < chips.length; i++) chips[i].classList.remove('active');
  chipEl.classList.add('active');
  var customInput = container.querySelector('.fn-form-option-custom');
  if (customInput) customInput.value = '';
}

function selectOptionCustom(paramName, inputEl) {
  var container = document.getElementById('fnField_' + paramName);
  if (!container) return;
  var val = inputEl.value.trim();
  if (val) {
    container.dataset.value = val;
    var chips = container.querySelectorAll('.fn-form-option-chip');
    for (var i = 0; i < chips.length; i++) chips[i].classList.remove('active');
  }
}

function submitFnForm(fnName) {
  if (isRunning) return;
  var fn = availableFunctions.find(function(f) { return f.name === fnName; });
  if (!fn) return;

  // work_dir is always required — it's a runtime-level setting, not a param.
  var workdirEl = document.getElementById('fnField_work_dir');
  var workdirVal = workdirEl ? workdirEl.value.trim() : '';
  if (!workdirVal) {
    if (workdirEl) {
      workdirEl.classList.add('workdir-input-error');
      workdirEl.focus();
    }
    return;
  }

  var params = (fn.params_detail || []).filter(function(p) {
    if (p.name === 'runtime' || p.name === 'callback' || p.name === 'exec_runtime' || p.name === 'review_runtime') return false;
    if (p.hidden) return false;
    return true;
  });

  var parts = ['run', fnName];
  for (var i = 0; i < params.length; i++) {
    var p = params[i];
    var el = document.getElementById('fnField_' + p.name);
    if (!el) continue;

    var val;
    if (el.dataset.value !== undefined) {
      val = el.dataset.value;
    } else {
      val = el.value.trim();
    }

    if (!val && !p.required) continue;
    if (!val && p.required) {
      el.style.borderColor = 'var(--accent-red)';
      if (el.focus) el.focus();
      return;
    }
    if (val.indexOf(' ') !== -1 || val.indexOf('"') !== -1) {
      parts.push(p.name + '=' + JSON.stringify(val));
    } else {
      parts.push(p.name + '=' + val);
    }
  }

  // Append work_dir last so user-facing command text keeps function params first.
  if (workdirVal.indexOf(' ') !== -1 || workdirVal.indexOf('"') !== -1) {
    parts.push('work_dir=' + JSON.stringify(workdirVal));
  } else {
    parts.push('work_dir=' + workdirVal);
  }

  var command = parts.join(' ');
  closeFnForm();
  sendMessage(command);
}

// ===== Sidebar section toggles (shared across all pages) =====

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

function doRefreshFunctions(btn) {
  if (btn.classList.contains('spinning')) return;
  var svg = btn.querySelector('svg');
  if (!svg) return;
  btn.classList.add('spinning');
  refreshFunctions();
  svg.addEventListener('animationend', function handler() {
    svg.removeEventListener('animationend', handler);
    btn.classList.remove('spinning');
    var orig = btn.innerHTML;
    btn.innerHTML = '&#10003;';
    btn.classList.add('done');
    setTimeout(function() {
      btn.innerHTML = orig;
      btn.classList.remove('done');
    }, 800);
  });
}
