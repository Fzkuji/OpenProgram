// ===== Chat Messaging =====
//
// The send path (`sendMessage` + user/assistant/runtime bubble builders)
// moved to the React composer — see web/components/chat/composer/
// legacy-send.ts and the chat-stream reducer. What remains here is the
// retry / pause-retry / follow-up glue still called from legacy WS
// handlers and React components.

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

// ===== Retry =====

// Per-node retry is handled by the React <ExecutionTree /> retry panel
// now (sends `retry_node` directly). The legacy node-detail panel in
// ui.js still generates an `onclick="rerunFromNode(...)"` button, so
// this no-op stub stays until ui.js is migrated.
function rerunFromNode(path) {}

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
  if (!currentSessionId) return;
  fetch('/api/stop', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: currentSessionId }),
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
