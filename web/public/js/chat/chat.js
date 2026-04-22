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
      conv_id: currentConvId,
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
      conv_id: currentConvId,
      thinking_effort: _thinkingEffort,
      exec_thinking_effort: _execThinkingEffort,
      tools: !!window._toolsEnabled
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

function retryChatQuery(text, btn) {
  if (!text || isRunning) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (btn) btn.disabled = true;
  var bubble = btn ? btn.closest('.message.assistant') : null;
  if (bubble && bubble.parentNode) bubble.parentNode.removeChild(bubble);

  setRunning(true);
  ws.send(JSON.stringify({
    action: 'chat',
    text: text,
    conv_id: currentConvId,
    thinking_effort: _thinkingEffort,
    exec_thinking_effort: _execThinkingEffort,
    tools: !!window._toolsEnabled
  }));
  var msgId = 'pending_' + Date.now();
  addAssistantPlaceholder(msgId);
}

function _injectPauseRetryButtons() {
  var blocks = document.querySelectorAll('.runtime-block[data-function]');
  blocks.forEach(function(block) {
    if (block.querySelector('.pause-retry-footer')) return;
    if (block.querySelector('.runtime-block-footer')) return;
    var fn = block.getAttribute('data-function');
    if (!fn) return;
    var footer = document.createElement('div');
    footer.className = 'runtime-block-footer pause-retry-footer';
    footer.innerHTML = '<div class="runtime-footer-left">' +
      '<button class="rerun-btn" onclick="stopAndRetry(\'' + escAttr(fn) + '\')">&#8634; Retry</button>' +
    '</div>';
    block.appendChild(footer);
  });
}

function _removePauseRetryButtons() {
  document.querySelectorAll('.pause-retry-footer').forEach(function(el) {
    if (el.parentNode) el.parentNode.removeChild(el);
  });
}

function stopAndRetry(funcName) {
  if (!currentConvId) return;
  fetch('/api/stop', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conv_id: currentConvId }),
  })
    .then(function(r) { return r.json(); })
    .then(function() {
      isPaused = false;
      isRunning = false;
      updateSendBtn();
      setTimeout(function() { retryCurrentBlock(funcName); }, 400);
    })
    .catch(function() {
      isPaused = false;
      isRunning = false;
      updateSendBtn();
    });
}

function retryCurrentBlock(funcName) {
  if (!currentConvId || !conversations[currentConvId]) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    addSystemMessage('Retry failed: not connected to server.');
    return;
  }

  var msgs = conversations[currentConvId].messages || [];
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
    conv_id: currentConvId,
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
  // Track this bubble so the chat_ack handler can stamp the
  // server-assigned msg_id on it (see init.js). Until then, the
  // action bar's retry/branch buttons stay present but inert — they
  // check for data-msg-id before firing.
  window._pendingUserBubble = div;
  appendToChat(div);
  if (typeof window.ensureMessageActions === 'function') {
    window.ensureMessageActions(div);
  }
  scrollToBottom();

  if (currentConvId && conversations[currentConvId]) {
    if (!conversations[currentConvId].messages) conversations[currentConvId].messages = [];
    conversations[currentConvId].messages.push({ role: 'user', content: text });
    updateContextStats(conversations[currentConvId].messages);
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

  if (currentConvId && conversations[currentConvId]) {
    if (!conversations[currentConvId].messages) conversations[currentConvId].messages = [];
    conversations[currentConvId].messages.push({ role: 'user', content: rawText, display: 'runtime' });
    updateContextStats(conversations[currentConvId].messages);
  }
}

