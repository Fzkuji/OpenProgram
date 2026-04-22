// ===== Response Handling =====

function handleChatResponse(data) {
  var type = data.type;
  console.log('[DEBUG] handleChatResponse type:', type, 'display:', data.display, 'function:', data.function);

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
  if (currentConvId && conversations[currentConvId]) {
    if (!conversations[currentConvId].messages) conversations[currentConvId].messages = [];
    var storedMsg = {
      role: 'assistant',
      content: data.content || '',
      type: type,
      function: data.function || null,
      display: isRuntimeResult ? 'runtime' : undefined,
      blocks: (data.blocks && data.blocks.length) ? data.blocks : undefined
    };
    if (type === 'result' && data.function) {
      storedMsg.attempts = [{
        content: data.content || '',
        tree: data.context_tree || null,
        timestamp: Date.now() / 1000
      }];
      storedMsg.current_attempt = 0;
    }
    conversations[currentConvId].messages.push(storedMsg);
    updateContextStats(conversations[currentConvId].messages);
  }

  // Update conversation title
  if (currentConvId && conversations[currentConvId]) {
    if (!conversations[currentConvId].title || conversations[currentConvId].title === 'New conversation') {
      var msgs = conversations[currentConvId].messages;
      if (msgs.length > 0) {
        conversations[currentConvId].title = msgs[0].content.slice(0, 50);
        renderConversations();
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
    if (currentConvId && conversations[currentConvId]) {
      var rebuilt = extractMessagesFromTree(ct);
      conversations[currentConvId].messages = rebuilt;
      renderConversationMessages(conversations[currentConvId]);
    }
  }
  scrollToBottom();
}

// Render a single stream event inside the current assistant bubble for a
// plain chat (no runtime_pending block). Supports three event types:
//   text      — streamed reply, rendered as markdown in .chat-text
//   thinking  — reasoning tokens, folded into a collapsible .chat-thinking
//   tool_use  — tool call, shown as a collapsible .chat-tool
//   tool_result — result for a prior tool_use, filled into matching .chat-tool
function _renderChatStreamEvent(evt) {
  var pendingKeys = Object.keys(pendingResponses);
  if (!pendingKeys.length) return;
  var el = pendingResponses[pendingKeys[0]];
  if (!el) return;

  var ti = el.querySelector('.typing-indicator');
  if (ti) ti.remove();

  // Each bubble gets a single scaffold: thinking block + tools block + text.
  // Order matches how the LLM emits them (reason first, tools, final text),
  // but CSS keeps thinking and tools visually "above" the answer regardless.
  var scaffold = el.querySelector('.chat-stream-body');
  if (!scaffold) {
    scaffold = document.createElement('div');
    scaffold.className = 'chat-stream-body';
    scaffold.innerHTML =
      '<div class="chat-thinking" data-collapsed="1" style="display:none">' +
        '<button type="button" class="chat-fold-btn" onclick="_toggleChatFold(this)" onmousedown="event.preventDefault()">' +
          '<span class="chat-fold-caret">&#9654;</span>' +
          '<span class="chat-fold-label">Thinking</span>' +
          '<span class="chat-fold-elapsed"></span>' +
        '</button>' +
        '<div class="chat-fold-content"></div>' +
      '</div>' +
      '<div class="chat-tools"></div>' +
      '<div class="chat-text message-content"></div>';
    el.appendChild(scaffold);
  }

  if (evt.type === 'text') {
    var textEl = scaffold.querySelector('.chat-text');
    if (!textEl.dataset.streamText) textEl.dataset.streamText = '';
    textEl.dataset.streamText += (evt.text || '');
    textEl.innerHTML = renderMd(textEl.dataset.streamText);
  } else if (evt.type === 'thinking') {
    var think = scaffold.querySelector('.chat-thinking');
    think.style.display = '';
    var tc = think.querySelector('.chat-fold-content');
    if (!tc.dataset.text) tc.dataset.text = '';
    tc.dataset.text += (evt.text || '');
    tc.textContent = tc.dataset.text;
    var elapsed = think.querySelector('.chat-fold-elapsed');
    if (evt.elapsed) elapsed.textContent = '· ' + evt.elapsed + 's';
  } else if (evt.type === 'tool_use') {
    var tools = scaffold.querySelector('.chat-tools');
    var callId = evt.tool_call_id || ('t_' + Date.now());
    var tool = document.createElement('div');
    tool.className = 'chat-tool';
    tool.dataset.callId = callId;
    tool.dataset.collapsed = '1';
    tool.innerHTML =
      '<button type="button" class="chat-fold-btn" onclick="_toggleChatFold(this)" onmousedown="event.preventDefault()">' +
        '<span class="chat-fold-caret">&#9654;</span>' +
        '<span class="chat-fold-label"><span class="chat-tool-name">' + escHtml(evt.tool || '?') + '</span>' +
          '<span class="chat-tool-args">(' + escHtml((evt.input || '').slice(0, 80)) + ')</span></span>' +
        '<span class="chat-fold-elapsed chat-tool-status">running…</span>' +
      '</button>' +
      '<div class="chat-fold-content">' +
        '<div class="chat-tool-section"><div class="chat-tool-section-label">args</div><pre class="chat-tool-pre">' + escHtml(evt.input || '') + '</pre></div>' +
        '<div class="chat-tool-section chat-tool-result-section" style="display:none"><div class="chat-tool-section-label">result</div><pre class="chat-tool-pre chat-tool-result"></pre></div>' +
      '</div>';
    tools.appendChild(tool);
  } else if (evt.type === 'tool_result') {
    var callId2 = evt.tool_call_id || '';
    var tool2 = scaffold.querySelector('.chat-tool[data-call-id="' + CSS.escape(callId2) + '"]');
    if (!tool2) {
      // Result arrived without a matching tool_use (shouldn't happen, but
      // degrade gracefully by creating a result-only block).
      var tools2 = scaffold.querySelector('.chat-tools');
      tool2 = document.createElement('div');
      tool2.className = 'chat-tool';
      tool2.dataset.callId = callId2 || ('t_' + Date.now());
      tool2.dataset.collapsed = '1';
      tool2.innerHTML =
        '<button type="button" class="chat-fold-btn" onclick="_toggleChatFold(this)" onmousedown="event.preventDefault()">' +
          '<span class="chat-fold-caret">&#9654;</span>' +
          '<span class="chat-fold-label"><span class="chat-tool-name">' + escHtml(evt.tool || '?') + '</span></span>' +
          '<span class="chat-fold-elapsed chat-tool-status"></span>' +
        '</button>' +
        '<div class="chat-fold-content">' +
          '<div class="chat-tool-section chat-tool-result-section"><div class="chat-tool-section-label">result</div><pre class="chat-tool-pre chat-tool-result"></pre></div>' +
        '</div>';
      tools2.appendChild(tool2);
    }
    var status = tool2.querySelector('.chat-tool-status');
    if (status) status.textContent = evt.is_error ? 'error' : (evt.elapsed ? '· ' + evt.elapsed + 's' : 'done');
    if (evt.is_error) tool2.classList.add('is-error');
    var section = tool2.querySelector('.chat-tool-result-section');
    if (section) section.style.display = '';
    var resultPre = tool2.querySelector('.chat-tool-result');
    if (resultPre) resultPre.textContent = evt.result || '';
  }

  scrollToBottom();
}

// Rebuild the streamed scaffold HTML from persisted blocks. Used when
// reloading a conversation — the live DOM is gone but msg.blocks has
// everything needed to regenerate the same collapsible layout.
function _renderAssistantBlocks(blocks, finalText) {
  var thinking = '';
  var toolsHtml = '';
  blocks.forEach(function(b) {
    if (b.type === 'thinking' && b.text) {
      thinking =
        '<div class="chat-thinking" data-collapsed="1">' +
          '<button type="button" class="chat-fold-btn" onclick="_toggleChatFold(this)" onmousedown="event.preventDefault()">' +
            '<span class="chat-fold-caret">&#9654;</span>' +
            '<span class="chat-fold-label">Thinking</span>' +
          '</button>' +
          '<div class="chat-fold-content">' + escHtml(b.text) + '</div>' +
        '</div>';
    } else if (b.type === 'tool') {
      var errCls = b.is_error ? ' is-error' : '';
      var statusTxt = b.is_error ? 'error' : 'done';
      var argsPreview = escHtml((b.input || '').slice(0, 80));
      var hasResult = b.result !== undefined && b.result !== null && b.result !== '';
      toolsHtml +=
        '<div class="chat-tool' + errCls + '" data-collapsed="1" data-call-id="' + escAttr(b.tool_call_id || '') + '">' +
          '<button type="button" class="chat-fold-btn" onclick="_toggleChatFold(this)" onmousedown="event.preventDefault()">' +
            '<span class="chat-fold-caret">&#9654;</span>' +
            '<span class="chat-fold-label"><span class="chat-tool-name">' + escHtml(b.tool || '?') + '</span>' +
              '<span class="chat-tool-args">(' + argsPreview + ')</span></span>' +
            '<span class="chat-fold-elapsed chat-tool-status">' + escHtml(statusTxt) + '</span>' +
          '</button>' +
          '<div class="chat-fold-content">' +
            '<div class="chat-tool-section"><div class="chat-tool-section-label">args</div><pre class="chat-tool-pre">' + escHtml(b.input || '') + '</pre></div>' +
            (hasResult
              ? '<div class="chat-tool-section chat-tool-result-section"><div class="chat-tool-section-label">result</div><pre class="chat-tool-pre chat-tool-result">' + escHtml(String(b.result)) + '</pre></div>'
              : '') +
          '</div>' +
        '</div>';
    }
  });
  var toolsBlock = toolsHtml ? ('<div class="chat-tools">' + toolsHtml + '</div>') : '<div class="chat-tools"></div>';
  return '<div class="chat-stream-body">' +
      thinking +
      toolsBlock +
      '<div class="chat-text message-content">' + renderMd(finalText || '') + '</div>' +
    '</div>';
}

function _toggleChatFold(btn) {
  var parent = btn.parentElement;
  if (!parent) return;
  var collapsed = parent.dataset.collapsed === '1';
  parent.dataset.collapsed = collapsed ? '0' : '1';
}

function _handleStreamEvent(data) {
  var evt = data.event || {};

  // Plain chat mode: no runtime_pending block (or one that was spawned
  // by _handleRunningTask for a `_chat` task during a reconnect). Stream
  // text deltas directly into the assistant placeholder so the response
  // appears word-by-word.
  var pendingBlock = document.getElementById('runtime_pending');
  var isChatStream = data.function === '_chat';
  if (!pendingBlock || isChatStream) {
    _renderChatStreamEvent(evt);
    return;
  }

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

  if (currentConvId && conversations[currentConvId]) {
    var msgs = conversations[currentConvId].messages || [];
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
    if (currentConvId && conversations[currentConvId]) {
      var msgs = conversations[currentConvId].messages || [];
      for (var ti = msgs.length - 1; ti >= 0; ti--) {
        if (msgs[ti].role === 'assistant' && msgs[ti].function === data.function) {
          conversations[currentConvId].messages = msgs.slice(0, ti + 1);
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
  if (!runtimeParams && currentConvId && conversations[currentConvId]) {
    var msgs = conversations[currentConvId].messages || [];
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
  // _handleRunningTask creates a `runtime_pending` bubble when the server
  // reports an in-flight `_chat` task (e.g. after a reconnect / load race).
  // That's a second placeholder competing with pendingResponses[*] — drop it
  // so the reply lands in a single bubble.
  var ghostChat = document.getElementById('runtime_pending');
  if (ghostChat && ghostChat.classList.contains('bot')) {
    ghostChat.remove();
  }

  var pendingKeys = Object.keys(pendingResponses);
  var targetEl = null;
  if (pendingKeys.length > 0) {
    var key = pendingKeys[0];
    targetEl = pendingResponses[key];
    delete pendingResponses[key];
  }

  if (targetEl && !document.body.contains(targetEl)) {
    // Placeholder was detached by a conversation_loaded re-render. Re-attach
    // the same node so the reply lands in one bubble, not two.
    appendToChat(targetEl);
  } else if (!targetEl) {
    targetEl = document.createElement('div');
    targetEl.className = 'message assistant';
    appendToChat(targetEl);
  }

  var contentHtml = '';
  if (type === 'error') {
    contentHtml = '<div class="error-content">' + escHtml(data.content) + '</div>';
    if (data.retry_query) {
      var retryAttr = escAttr(data.retry_query);
      contentHtml += '<button class="rerun-btn" onclick="retryChatQuery(\'' + retryAttr + '\', this)">&#8634; Retry</button>';
    }
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
  // Stamp msg_id so the hover copy/retry/branch bar can target the
  // right server message. Server broadcasts data.msg_id = the user
  // turn id; our /api/chat/retry endpoint walks back to the nearest
  // user message anyway, so stamping the user id on the assistant
  // bubble is correct.
  if (data.msg_id) {
    targetEl.setAttribute('data-msg-id', data.msg_id);
    if (typeof window.ensureMessageActions === 'function') {
      window.ensureMessageActions(targetEl);
    }
  }

  // If the bubble already has a streamed scaffold (thinking/tools/text were
  // being populated live), keep those sections and only refresh the final
  // text block with the authoritative content. Wiping innerHTML here would
  // throw away all the folded thinking/tool blocks the user just watched.
  var existingScaffold = targetEl.querySelector('.chat-stream-body');
  if (type === 'result' && existingScaffold) {
    if (!targetEl.querySelector('.message-header')) {
      var hdr = document.createElement('div');
      hdr.className = 'message-header';
      hdr.innerHTML =
        '<div class="message-avatar bot-avatar">A</div>' +
        '<div class="message-sender">Agentic</div>';
      targetEl.insertBefore(hdr, targetEl.firstChild);
    }
    var finalText = existingScaffold.querySelector('.chat-text');
    if (finalText) {
      finalText.innerHTML = renderMd(data.content || '');
      delete finalText.dataset.streamText;
    }
  } else {
    targetEl.innerHTML =
      '<div class="message-header">' +
        '<div class="message-avatar bot-avatar">A</div>' +
        '<div class="message-sender">Agentic</div>' +
      '</div>' +
      contentHtml;
  }
  scrollToBottom();
}
