// ===== Chat Messaging =====

function buildRuntimeBlockHtml(funcName, params, contentHtml, treeHtml, attemptNavHtml, rerunHtml, usage) {
  var tempDiv = document.createElement('div');
  tempDiv.innerHTML = contentHtml;
  var plainPreview = (tempDiv.textContent || '').trim().substring(0, 60);
  if (plainPreview.length >= 60) plainPreview += '...';

  var headerHtml = '<div class="runtime-block-header" onclick="toggleRuntimeBlock(this)">' +
    '<span class="runtime-icon">&#9654;</span>' +
    '<span class="runtime-func">' + escHtml(funcName) + (params ? '(<span class="runtime-params">' + escHtml(params) + '</span>)' : '()') + '</span>' +
    '<span class="runtime-result-preview">-> ' + escHtml(plainPreview) + '</span>' +
  '</div>';
  var bodyHtml = '<div class="runtime-block-body"><div class="runtime-block-content">' +
    '<div class="runtime-result"><span class="runtime-return-label">return:</span></div>' +
    '<div class="runtime-output">' + contentHtml + '</div>' +
    (treeHtml || '') +
  '</div></div>';
  var usageFooter = formatUsageFooterLabel(usage);
  var footerHtml = '';
  if (rerunHtml || attemptNavHtml || usageFooter) {
    footerHtml = '<div class="runtime-block-footer">' +
      '<div class="runtime-footer-left">' + (rerunHtml || '') + '</div>' +
      '<div class="runtime-footer-center">' + (attemptNavHtml || '') + '</div>' +
      '<div class="runtime-footer-right">' + usageFooter + '</div>' +
    '</div>';
  }
  return headerHtml + bodyHtml + footerHtml;
}

// ===== Follow-up =====

function submitFollowUp() {
  var inp = document.getElementById('followUpInput');
  if (!inp) return;
  var answer = inp.value.trim();
  if (!answer) return;
  var container = inp.closest('.follow-up-container');
  if (container) container.remove();
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify({
      action: 'follow_up_answer',
      session_id: currentSessionId,
      answer: answer,
    }));
  }
}

// ===== Send & Retry =====

function sendMessage(textOverride) {
  if (isRunning) return;

  var input = document.getElementById('chatInput');
  var text = textOverride ? textOverride.trim() : input.value.trim();
  if (!text) return;

  if (text.toLowerCase().startsWith('run ')) _lastRunCommand = text;

  setWelcomeVisible(false);
  closeFnForm();

  var isRunCommand = /^(run\s|create\s|fix\s)/i.test(text);

  if (isRunCommand) {
    var parsed = parseRunCommandForDisplay(text);
    addRuntimeBlockPending(text, parsed.funcName, parsed.params);
  } else {
    addUserMessage(text);
  }
  if (!textOverride) {
    input.value = '';
    autoResize(input);
  }

  setRunning(true);
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      action: 'chat',
      text: text,
      session_id: currentSessionId,
      // New conversations land in the UI's current agent so the new
      // session file is created under that agent's sessions/ dir.
      agent_id: (typeof currentAgentId !== 'undefined' ? currentAgentId : null),
      thinking_effort: _thinkingEffort,
      exec_thinking_effort: _execThinkingEffort
    }));
  } else {
    var errDiv = document.createElement('div');
    errDiv.className = 'message assistant';
    errDiv.innerHTML = '<div class="error-content">WebSocket disconnected. Reconnecting...</div>';
    appendToChat(errDiv);
    return;
  }

  if (!isRunCommand) {
    var msgId = 'pending_' + Date.now();
    addAssistantPlaceholder(msgId);
  }
}

function rerunFunction() {
  if (!_lastRunCommand) return;
  var input = document.getElementById('chatInput');
  input.value = _lastRunCommand;
  input.focus();
  autoResize(input);
}

function rerunFromNode(path) {
  executeRetry(path);
}

function retryCurrentBlock(funcName) {
  if (!currentSessionId || !conversations[currentSessionId]) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    addSystemMessage('Retry failed: not connected to server.');
    return;
  }

  var msgs = conversations[currentSessionId].messages || [];
  var userCmd = null;

  // 1) Look for user message with display:'runtime' matching funcName
  for (var i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === 'user' && msgs[i].display === 'runtime') {
      var parsed = parseRunCommandForDisplay(msgs[i].content || '');
      if (parsed.funcName === funcName || !funcName) {
        userCmd = msgs[i].original_content || msgs[i].content;
        break;
      }
    }
  }

  // 2) Fallback: look for any user message that looks like a run command
  if (!userCmd) {
    for (var j = msgs.length - 1; j >= 0; j--) {
      if (msgs[j].role === 'user') {
        var content = msgs[j].content || '';
        if (/^(run\s|create\s|fix\s)/i.test(content)) {
          var parsed2 = parseRunCommandForDisplay(content);
          if (!funcName || parsed2.funcName === funcName) {
            userCmd = msgs[j].original_content || content;
            break;
          }
        }
      }
    }
  }

  // 3) Fallback: _lastRunCommand
  if (!userCmd && _lastRunCommand) userCmd = _lastRunCommand;

  // 4) Last resort: reconstruct from funcName
  if (!userCmd && funcName) userCmd = 'run ' + funcName;

  if (!userCmd) return;

  // If funcName is empty, try to extract it from userCmd
  if (!funcName) {
    var cmdParsed = parseRunCommandForDisplay(userCmd);
    funcName = cmdParsed.funcName || '';
  }

  var existingBlock = funcName ? document.querySelector('.runtime-block[data-function="' + funcName + '"]') : null;
  if (!existingBlock) {
    existingBlock = document.querySelector('.runtime-block.error') || document.querySelector('.runtime-block.interrupted');
  }
  if (existingBlock) {
    existingBlock.className = 'runtime-block runtime-block-pending';
    existingBlock.id = 'runtime_pending';
    existingBlock.setAttribute('data-function', funcName);
    var parsedDisplay = parseRunCommandForDisplay(userCmd);

    // Retry = fresh session, clear all previous attempts immediately
    existingBlock.innerHTML =
      '<div class="runtime-block-header">' +
        '<span class="runtime-icon">&#9654;</span>' +
        '<span class="runtime-func">' + escHtml(parsedDisplay.funcName) +
          (parsedDisplay.params ? '(<span class="runtime-params">' + escHtml(parsedDisplay.params) + '</span>)' : '()') +
        '</span>' +
      '</div>' +
      '<div class="runtime-block-body"><div class="runtime-block-content">' +
        '<div class="typing-indicator"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>' +
      '</div></div>';
  }

  setRunning(true);
  ws.send(JSON.stringify({
    action: 'retry_overwrite',
    session_id: currentSessionId,
    function: funcName,
    text: userCmd,
    thinking_effort: _thinkingEffort,
    exec_thinking_effort: _execThinkingEffort
  }));
}

// ===== Message Rendering =====

function addUserMessage(text) {
  var div = document.createElement('div');
  div.className = 'message user';
  div.innerHTML =
    '<div class="message-header">' +
      '<div class="message-avatar user-avatar">U</div>' +
      '<div class="message-sender">You</div>' +
    '</div>' +
    '<div class="message-content">' + escHtml(text) + '</div>';
  appendToChat(div);
  scrollToBottom();

  if (currentSessionId && conversations[currentSessionId]) {
    if (!conversations[currentSessionId].messages) conversations[currentSessionId].messages = [];
    conversations[currentSessionId].messages.push({ role: 'user', content: text });
    updateContextStats(conversations[currentSessionId].messages);
  }
}

function addAssistantPlaceholder(id) {
  var div = document.createElement('div');
  div.className = 'message assistant';
  div.id = 'msg_' + id;
  div.innerHTML =
    '<div class="message-header">' +
      '<div class="message-avatar bot-avatar">A</div>' +
      '<div class="message-sender">Agentic</div>' +
    '</div>' +
    '<div class="typing-indicator"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>';
  appendToChat(div);
  pendingResponses[id] = div;
  scrollToBottom();
}

function addAssistantMessage(text) {
  setWelcomeVisible(false);
  var div = document.createElement('div');
  div.className = 'message assistant';
  div.innerHTML =
    '<div class="message-header">' +
      '<div class="message-avatar bot-avatar">A</div>' +
      '<div class="message-sender">Agentic</div>' +
    '</div>' +
    '<div class="message-content">' + escHtml(text) + '</div>';
  appendToChat(div);
  scrollToBottom();
}

function addRuntimeBlockPending(rawText, funcName, params) {
  var div = document.createElement('div');
  div.className = 'runtime-block runtime-block-pending';
  div.id = 'runtime_pending';
  var headerHtml = '<div class="runtime-block-header" onclick="toggleRuntimeBlock(this)">' +
    '<span class="runtime-icon">&#9654;</span>' +
    '<span class="runtime-func">' + escHtml(funcName) + (params ? '(<span class="runtime-params">' + escHtml(params) + '</span>)' : '()') + '</span>' +
  '</div>';
  div.innerHTML = headerHtml +
    '<div class="runtime-block-body"><div class="runtime-block-content">' +
      '<div class="typing-indicator"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>' +
    '</div></div>';
  appendToChat(div);
  scrollToBottom();

  if (currentSessionId && conversations[currentSessionId]) {
    if (!conversations[currentSessionId].messages) conversations[currentSessionId].messages = [];
    conversations[currentSessionId].messages.push({ role: 'user', content: rawText, display: 'runtime' });
    updateContextStats(conversations[currentSessionId].messages);
  }
}

// ===== Response Handling =====

function handleChatResponse(data) {
  var type = data.type;

  if (type === 'context_stats') {
    _handleContextStats(data);
    return;
  }
  if (type === 'status') {
    _handleStatusResponse(data);
    return;
  }
  if (type === 'stream_event' && data.event) {
    _handleStreamEvent(data);
    return;
  }
  if (type === 'follow_up_question') {
    _handleFollowUpQuestion(data);
    return;
  }
  if (type === 'tree_update') {
    _handleTreeUpdate(data);
    return;
  }

  // Final response (result or error) -- task done
  setRunning(false);
  loadAgentSettings();

  // Tear down the elapsed-time ticker. Any surviving data-running attribute
  // after a terminal message (result / error / cancelled) is a zombie — the
  // tree won't receive further updates, so the numbers would tick forever.
  if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
  document.querySelectorAll('.node-duration[data-running]').forEach(function(el) {
    el.removeAttribute('data-running');
  });

  if (type === 'retry_result' && data.function && data.attempts) {
    _handleRetryResult(data);
    return;
  }

  // Legacy retry result
  if (data.is_retry && data.context_tree && (data.context_tree.path || data.context_tree.name)) {
    var ct = data.context_tree;
    var rootKey = ct.path || ct.name;
    var idx = trees.findIndex(function(t) { return t.path === rootKey || t.name === ct.name; });
    if (idx >= 0) { trees[idx] = ct; } else { trees.push(ct); }
    expandedNodes.add(rootKey);
  }

  // Remove status line
  var statusLine = document.getElementById('currentStatusLine');
  if (statusLine) statusLine.remove();

  var isRuntimeResult = data.display === 'runtime' || (data.function && data.function !== 'chat');

  if (isRuntimeResult) {
    _handleRuntimeResult(data, type);
  } else {
    _handleChatResult(data, type);
  }

  // Store assistant message
  if (currentSessionId && conversations[currentSessionId]) {
    if (!conversations[currentSessionId].messages) conversations[currentSessionId].messages = [];
    var storedMsg = {
      role: 'assistant',
      content: data.content || '',
      type: type,
      function: data.function || null,
      display: isRuntimeResult ? 'runtime' : undefined
    };
    if (type === 'result' && data.function) {
      storedMsg.attempts = [{
        content: data.content || '',
        tree: data.context_tree || null,
        timestamp: Date.now() / 1000
      }];
      storedMsg.current_attempt = 0;
    }
    conversations[currentSessionId].messages.push(storedMsg);
    updateContextStats(conversations[currentSessionId].messages);
  }

  // Update conversation title
  if (currentSessionId && conversations[currentSessionId]) {
    if (!conversations[currentSessionId].title || conversations[currentSessionId].title === 'New conversation') {
      var msgs = conversations[currentSessionId].messages;
      if (msgs.length > 0) {
        conversations[currentSessionId].title = msgs[0].content.slice(0, 50);
        renderSessions();
      }
    }
  }
}

// --- Internal response handlers ---

function _handleContextStats(data) {
  var el = document.getElementById('contextStats');
  if (!el) return;

  console.log('[DEBUG] context_stats raw data:', JSON.stringify(data));

  var chat = data.chat || {};
  if (!data.chat && (data.input_tokens || data.output_tokens)) {
    chat = { input_tokens: data.input_tokens || 0, output_tokens: data.output_tokens || 0, cache_read: data.cache_read || 0 };
  }

  console.log('[DEBUG] chat usage object:', JSON.stringify(chat));

  var provider = data.provider || '';
  var result = _buildUsageText(chat, provider);
  console.log('[DEBUG] result:', JSON.stringify(result), 'provider:', provider);
  if (result) {
    var t = typeof result === 'string' ? result : result.text;
    var tip = typeof result === 'object' && result.tooltip ? result.tooltip : '';
    el.textContent = 'chat: ' + t;
    el.title = tip;
  } else {
    el.textContent = '';
    el.title = '';
  }
}

function _handleStatusResponse(data) {
  if (data.context_tree) {
    var ct = data.context_tree;
    var rootKey = ct.path || ct.name;
    var idx = trees.findIndex(function(t) { return t.path === rootKey || t.name === ct.name; });
    if (idx >= 0) { trees[idx] = ct; } else { trees.push(ct); }
    if (currentSessionId && conversations[currentSessionId]) {
      var rebuilt = extractMessagesFromTree(ct);
      conversations[currentSessionId].messages = rebuilt;
      renderSessionMessages(conversations[currentSessionId]);
    }
  }
  scrollToBottom();
}

function _handleStreamEvent(data) {
  var pendingBlock = document.getElementById('runtime_pending');
  if (!pendingBlock) return;
  var content = pendingBlock.querySelector('.runtime-block-content');
  if (!content) return;

  var termWrap = content.querySelector('.stream-terminal-wrap');
  var terminal;
  if (!termWrap) {
    termWrap = document.createElement('div');
    termWrap.className = 'stream-terminal-wrap';
    termWrap.innerHTML =
      '<div class="stream-terminal-header" onclick="this.parentElement.classList.toggle(\'collapsed\')">' +
        '<span class="stream-terminal-toggle">&#9654;</span>' +
        '<span>CLI Output</span>' +
      '</div>' +
      '<div class="stream-terminal"></div>';
    content.appendChild(termWrap);
  }
  terminal = termWrap.querySelector('.stream-terminal');
  var evt = data.event;
  var line = document.createElement('div');
  var time = '<span class="stream-time">[' + evt.elapsed + 's]</span> ';
  if (evt.type === 'text') {
    line.innerHTML = time + '<span class="stream-text">' + escHtml(evt.text || '') + '</span>';
  } else if (evt.type === 'tool_use') {
    line.innerHTML = time + '<span class="stream-tool">$ ' + escHtml(evt.tool || '?') + '</span> <span class="stream-text">' + escHtml(evt.input || '') + '</span>';
  } else if (evt.type === 'status') {
    line.innerHTML = time + '<span class="stream-status">' + escHtml(evt.text || '') + '</span>';
  } else {
    line.innerHTML = time + escHtml(evt.text || evt.type || '');
  }
  terminal.appendChild(line);
  // Cap line count to keep DOM light on long Codex / Claude Code runs.
  var MAX_STREAM_LINES = 500;
  while (terminal.childElementCount > MAX_STREAM_LINES) {
    terminal.removeChild(terminal.firstElementChild);
  }
  terminal.scrollTop = terminal.scrollHeight;
}

function _handleFollowUpQuestion(data) {
  var pendingBlock = document.getElementById('runtime_pending');
  if (!pendingBlock) return;
  var contentArea = pendingBlock.querySelector('.runtime-block-content') || pendingBlock.querySelector('.runtime-block-body');
  if (!contentArea) return;

  var existing = contentArea.querySelector('.follow-up-container');
  if (existing) existing.remove();

  var fuHtml =
    '<div class="follow-up-container" style="margin:12px 0;padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--bg-secondary)">' +
      '<div style="color:var(--accent-yellow);font-weight:600;margin-bottom:8px">&#9888; Follow-up Question</div>' +
      '<div style="margin-bottom:10px;color:var(--text-primary)">' + escHtml(data.question) + '</div>' +
      '<div style="display:flex;gap:8px">' +
        '<input type="text" id="followUpInput" placeholder="Type your answer..." ' +
          'style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:6px;background:var(--bg-primary);color:var(--text-primary);font-size:14px" ' +
          'onkeydown="if(event.key===\'Enter\')submitFollowUp()">' +
        '<button onclick="submitFollowUp()" ' +
          'style="padding:8px 16px;border:none;border-radius:6px;background:var(--accent-blue);color:white;cursor:pointer;font-size:14px">Submit</button>' +
      '</div>' +
    '</div>';
  contentArea.insertAdjacentHTML('beforeend', fuHtml);
  var inp = document.getElementById('followUpInput');
  if (inp) inp.focus();
  scrollToBottom();
}

function _handleTreeUpdate(data) {
  if (!data.tree) return;
  var treeJson = JSON.stringify(data.tree);
  if (treeJson === window._lastTreeJson) return;
  window._lastTreeJson = treeJson;

  var pendingBlock = document.getElementById('runtime_pending');
  if (!pendingBlock) return;
  var content = pendingBlock.querySelector('.runtime-block-content');
  if (!content) {
    var body = pendingBlock.querySelector('.runtime-block-body');
    if (body) { body.innerHTML = '<div class="runtime-block-content"></div>'; content = body.firstChild; }
  }
  if (!content) return;

  var treeId = 'itree_live_' + (data.function || 'run').replace(/[^a-zA-Z0-9]/g, '_');
  if (!content.querySelector('.inline-tree')) {
    var existingFU = content.querySelector('.follow-up-container');
    content.innerHTML =
      '<div class="runtime-result"><span class="runtime-return-label">return:</span></div>' +
      '<div class="typing-indicator"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>' +
      renderInlineTree(data.tree, treeId);
    if (existingFU) content.appendChild(existingFU);
  } else {
    var treeBody = content.querySelector('.inline-tree-body');
    if (treeBody) {
      function _expandAll(n) {
        if (n.path) expandedNodes.add(n.path);
        if (n.children) n.children.forEach(_expandAll);
      }
      _expandAll(data.tree);
      treeBody.innerHTML = renderTreeNode(data.tree);
    }
    var headerSpan = content.querySelector('.inline-tree-header > span:first-child');
    if (headerSpan) {
      var hasRunning = _treeHasRunning(data.tree);
      var statusIcon = hasRunning
        ? '<span class="pulse" style="color:var(--accent-blue)">&#9679;</span> '
        : '<span style="color:var(--accent-cyan)">&#9670;</span> ';
      headerSpan.innerHTML = statusIcon + 'Execution Tree';
    }
  }
  scrollToBottom();
  startElapsedTimer();
}

function _handleRetryResult(data) {
  setRunning(false);
  loadAgentSettings();

  if (data.context_tree && (data.context_tree.path || data.context_tree.name)) {
    var ct = data.context_tree;
    var rootKey = ct.path || ct.name;
    var idx = trees.findIndex(function(t) { return t.path === rootKey || t.name === ct.name; });
    if (idx >= 0) { trees[idx] = ct; } else { trees.push(ct); }
    expandedNodes.add(rootKey);
  }

  var statusLine = document.getElementById('currentStatusLine');
  if (statusLine) statusLine.remove();
  var pendingKeys = Object.keys(pendingResponses);
  for (var pi = 0; pi < pendingKeys.length; pi++) {
    var pel = pendingResponses[pendingKeys[pi]];
    if (pel && pel.parentNode) pel.parentNode.removeChild(pel);
    delete pendingResponses[pendingKeys[pi]];
  }

  if (currentSessionId && conversations[currentSessionId]) {
    var msgs = conversations[currentSessionId].messages || [];
    for (var mi = msgs.length - 1; mi >= 0; mi--) {
      if (msgs[mi].role === 'assistant' && msgs[mi].function === data.function) {
        msgs[mi].attempts = data.attempts;
        msgs[mi].current_attempt = data.current_attempt;
        msgs[mi].content = data.content;
        break;
      }
    }
  }

  if (data.truncated) {
    var existingCheck = document.querySelector('[data-function="' + data.function + '"]');
    if (existingCheck) {
      while (existingCheck.nextElementSibling) {
        existingCheck.nextElementSibling.remove();
      }
    }
    if (currentSessionId && conversations[currentSessionId]) {
      var msgs = conversations[currentSessionId].messages || [];
      for (var ti = msgs.length - 1; ti >= 0; ti--) {
        if (msgs[ti].role === 'assistant' && msgs[ti].function === data.function) {
          conversations[currentSessionId].messages = msgs.slice(0, ti + 1);
          break;
        }
      }
    }
  }

  var existingEl = document.querySelector('[data-function="' + data.function + '"]');
  if (existingEl) {
    var curIdx = data.current_attempt;
    var total = data.attempts.length;
    var retryTree = data.context_tree || (data.attempts[curIdx] && data.attempts[curIdx].tree);

    if (existingEl.classList.contains('runtime-block')) {
      var resultContentHtml = renderMd(data.content || '');
      var treeHtml = '';
      var attemptNavHtml = '';
      var rerunHtml = '';
      if (retryTree && (retryTree.path || retryTree.name)) {
        treeHtml = renderInlineTree(retryTree, 'itree_retry_' + data.function.replace(/[^a-zA-Z0-9]/g, '_') + '_' + curIdx);
      }
      if (total > 1) {
        attemptNavHtml = renderAttemptNav(data.function, curIdx, total);
      }
      rerunHtml = '<button class="rerun-btn" onclick="retryCurrentBlock(\'' + escAttr(data.function) + '\')">&#8634; Retry</button>';
      var usageFooter = formatUsageFooterLabel(data.usage);
      var existingHeader = existingEl.querySelector('.runtime-block-header');
      var headerHtml = existingHeader ? existingHeader.outerHTML : '<div class="runtime-block-header"><span class="runtime-icon">&#9654;</span><span class="runtime-func">' + escHtml(data.function) + '</span>()</div>';
      var bodyHtml = '<div class="runtime-block-body"><div class="runtime-block-content"><div class="runtime-result">' + resultContentHtml + '</div>' + treeHtml + '</div></div>';
      var footerHtml = '';
      if (rerunHtml || attemptNavHtml || usageFooter) {
        footerHtml = '<div class="runtime-block-footer">' +
          '<div class="runtime-footer-left">' + rerunHtml + '</div>' +
          '<div class="runtime-footer-center">' + attemptNavHtml + '</div>' +
          '<div class="runtime-footer-right">' + usageFooter + '</div>' +
        '</div>';
      }
      existingEl.innerHTML = headerHtml + bodyHtml + footerHtml;
    } else {
      var cHtml = '<div class="message-content">';
      cHtml += '<div style="margin-bottom:4px"><span style="font-family:var(--font-mono);color:var(--accent-green);font-size:12px">' +
        escHtml(data.function) + '()</span> completed</div>';
      cHtml += renderMd(data.content || '');
      cHtml += '</div>';
      if (total > 1) {
        cHtml += renderAttemptNav(data.function, curIdx, total);
      }
      if (retryTree && (retryTree.path || retryTree.name)) {
        cHtml += renderInlineTree(retryTree, 'itree_retry_' + data.function.replace(/[^a-zA-Z0-9]/g, '_') + '_' + curIdx);
      }
      existingEl.innerHTML =
        '<div class="message-header">' +
          '<div class="message-avatar bot-avatar">A</div>' +
          '<div class="message-sender">Agentic</div>' +
        '</div>' + cHtml;
    }
  }
  scrollToBottom();
}

function _handleRuntimeResult(data, type) {
  window._lastTreeJson = null;
  var pendingBlock = document.getElementById('runtime_pending');
  if (!pendingBlock && data.function) {
    pendingBlock = document.querySelector('.runtime-block[data-function="' + data.function + '"]');
  }

  var pendingKeys = Object.keys(pendingResponses);
  for (var pi = 0; pi < pendingKeys.length; pi++) {
    var pel = pendingResponses[pendingKeys[pi]];
    if (pel && pel.parentNode) pel.parentNode.removeChild(pel);
    delete pendingResponses[pendingKeys[pi]];
  }

  var runtimeParams = '';
  if (pendingBlock) {
    var paramsSpan = pendingBlock.querySelector('.runtime-params');
    if (paramsSpan) runtimeParams = paramsSpan.textContent || '';
  }
  if (!runtimeParams && currentSessionId && conversations[currentSessionId]) {
    var msgs = conversations[currentSessionId].messages || [];
    for (var ri = msgs.length - 1; ri >= 0; ri--) {
      if (msgs[ri].role === 'user' && msgs[ri].display === 'runtime') {
        var parsed = parseRunCommandForDisplay(msgs[ri].content);
        runtimeParams = parsed.params;
        break;
      }
    }
  }

  var content = data.content || '';
  var resultContentHtml = renderMd(content);
  var treeHtml = '';
  var attemptNavHtml = '';
  var rerunHtml = data.function ? '<button class="rerun-btn" onclick="retryCurrentBlock(\'' + escAttr(data.function) + '\')">&#8634; Retry</button>' : '';

  if (data.context_tree && (data.context_tree.path || data.context_tree.name)) {
    var ct = data.context_tree;
    treeHtml = renderInlineTree(ct, 'itree_' + (data.function || 'result').replace(/[^a-zA-Z0-9]/g, '_'));
    var rootKey = ct.path || ct.name;
    var idx = trees.findIndex(function(t) { return t.path === rootKey || t.name === ct.name; });
    if (idx >= 0) { trees[idx] = ct; } else { trees.push(ct); }
    expandedNodes.add(rootKey);
  }

  if (data.attempts && data.attempts.length > 1) {
    attemptNavHtml = renderAttemptNav(data.function, data.current_attempt || 0, data.attempts.length);
  }

  var blockHtml = buildRuntimeBlockHtml(data.function, runtimeParams, resultContentHtml, treeHtml, attemptNavHtml, rerunHtml, data.usage);
  var blockClass = 'runtime-block' + (type === 'error' ? ' error' : '');

  if (pendingBlock) {
    pendingBlock.className = blockClass;
    pendingBlock.id = '';
    pendingBlock.setAttribute('data-function', data.function);
    pendingBlock.innerHTML = blockHtml;
  } else {
    // Check for existing completed block (retry result arriving after page refresh)
    var existingFnBlock = data.function ? document.querySelector('.runtime-block[data-function="' + data.function + '"]') : null;
    if (existingFnBlock) {
      existingFnBlock.className = blockClass;
      existingFnBlock.innerHTML = blockHtml;
    } else {
      var newBlock = document.createElement('div');
      newBlock.className = blockClass;
      newBlock.setAttribute('data-function', data.function);
      newBlock.innerHTML = blockHtml;
      appendToChat(newBlock);
    }
  }
  scrollToBottom();
}

function _handleChatResult(data, type) {
  var pendingKeys = Object.keys(pendingResponses);
  var targetEl = null;
  if (pendingKeys.length > 0) {
    var key = pendingKeys[0];
    targetEl = pendingResponses[key];
    delete pendingResponses[key];
  }

  if (!targetEl) {
    targetEl = document.createElement('div');
    targetEl.className = 'message assistant';
    appendToChat(targetEl);
  }

  var contentHtml = '';
  if (type === 'error') {
    contentHtml = '<div class="error-content">' + escHtml(data.content) + '</div>';
  } else if (type === 'result') {
    var content = data.content || '';
    contentHtml = '<div class="message-content">';
    if (data.function) {
      contentHtml += '<div style="margin-bottom:4px"><span style="font-family:var(--font-mono);color:var(--accent-green);font-size:12px">' +
        escHtml(data.function) + '()</span> completed</div>';
    }
    contentHtml += renderMd(content);
    contentHtml += '</div>';

    if (data.context_tree && (data.context_tree.path || data.context_tree.name)) {
      var ct = data.context_tree;
      contentHtml += renderInlineTree(ct, 'itree_' + (data.function || 'result').replace(/[^a-zA-Z0-9]/g, '_'));
      var rootKey = ct.path || ct.name;
      var idx = trees.findIndex(function(t) { return t.path === rootKey || t.name === ct.name; });
      if (idx >= 0) { trees[idx] = ct; } else { trees.push(ct); }
      expandedNodes.add(rootKey);
    }
  }

  if (data.function) {
    targetEl.setAttribute('data-function', data.function);
  }
  targetEl.innerHTML =
    '<div class="message-header">' +
      '<div class="message-avatar bot-avatar">A</div>' +
      '<div class="message-sender">Agentic</div>' +
    '</div>' +
    contentHtml;
  scrollToBottom();
}
