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
  if (type === 'user_message') {
    _handleInboundUserMessage(data);
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
  // Refresh token counts: the assistant turn just persisted a new
  // provider_usage row, so the branch's current_tokens + the topbar
  // chip both need to re-read from the server. Without this the UI
  // shows stale numbers until the user clicks somewhere or reloads.
  if (typeof window.refreshTokenBadge === 'function') {
    try { window.refreshTokenBadge(); } catch (e) {}
  }
  // The turn just appended new messages (and possibly created a new
  // branch tip). Force-refresh the branches cache so the right
  // sidebar visualization picks up new nodes without requiring a
  // session reload.
  if (typeof fetchBranches === 'function' && currentSessionId) {
    try {
      fetchBranches(currentSessionId, { force: true }).then(function () {
        if (typeof window._refreshBranchTokens === 'function') {
          try { window._refreshBranchTokens(); } catch (e) {}
        }
      });
    } catch (e) {}
  }

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
        if (typeof window.refreshStatusSource === 'function') {
          window.refreshStatusSource();
        }
      }
    }
  }
}

// --- Internal response handlers ---

function _handleInboundUserMessage(data) {
  // Only render when this user message belongs to the session the
  // browser is currently viewing. dispatcher broadcasts globally, so
  // every connected client gets every session's events.
  if (!data || !data.session_id || data.session_id !== currentSessionId) return;
  // Web-side sends already render an optimistic bubble locally — skip
  // the broadcast for that path to avoid double-rendering.
  if (data.source === 'web') return;
  // DOM-level dedup: if a bubble with this msg_id is already in the
  // transcript (we've seen this envelope before, or load_session
  // already rendered it), don't append again.
  if (data.msg_id && document.querySelector('.message[data-msg-id="' + data.msg_id + '"]')) {
    return;
  }
  if (typeof addUserMessage !== 'function') return;
  addUserMessage(data.content || '');
  var bubble = window._pendingUserBubble;
  if (bubble) {
    if (data.msg_id) bubble.setAttribute('data-msg-id', data.msg_id);
    if (data.peer_display) {
      var label = bubble.querySelector('.message-sender');
      if (label) label.textContent = data.peer_display;
    }
    window._pendingUserBubble = null;
  }
  // Hide the welcome screen if it's still up — fresh inbound message
  // means this session is no longer empty.
  if (typeof setWelcomeVisible === 'function') setWelcomeVisible(false);
}

function _handleContextStats(data) {
  var el = document.getElementById('contextStats');

  var chat = data.chat || {};
  if (!data.chat && (data.input_tokens || data.output_tokens)) {
    chat = { input_tokens: data.input_tokens || 0, output_tokens: data.output_tokens || 0, cache_read: data.cache_read || 0 };
  }

  // Record cache write timestamp so the token badge dot tracks TTL.
  var cacheWrite = chat.cache_write || data.cache_write_tokens || 0;
  if (cacheWrite > 0 && typeof currentSessionId !== 'undefined' && currentSessionId) {
    if (typeof window._recordCacheWrite === 'function') window._recordCacheWrite(currentSessionId);
  }

  // Feed the React store — this is what the composer's <ContextBadge />
  // actually renders from. The legacy #contextStats / _renderTokenBadge
  // DOM paths below are dead after the React migration (those nodes no
  // longer exist), so without this push the badge stays invisible.
  if (window.__sessionStore && typeof currentSessionId !== 'undefined' && currentSessionId) {
    try {
      window.__sessionStore.getState().setContextStats(
        currentSessionId,
        {
          input: chat.input_tokens || 0,
          output: chat.output_tokens || 0,
          cache_read: chat.cache_read || 0,
        },
        data.context_window || null,
      );
    } catch (e) { /* store not ready yet — a later stats event will land */ }
  }

  // Update token badge directly from WS data — no HTTP round-trip needed.
  if (typeof window._renderTokenBadge === 'function' && typeof currentSessionId !== 'undefined' && currentSessionId) {
    var wsTokenData = {
      current_tokens: data.current_tokens || (chat.input_tokens || 0) + (chat.output_tokens || 0),
      naive_sum: data.naive_sum || 0,
      context_window: data.context_window || 0,
      cache_hit_rate: data.cache_hit_rate || 0,
      cache_read_total: data.cache_read_total || chat.cache_read || 0,
      last_assistant_usage: data.last_assistant_usage || 0,
      last_assistant_input: data.last_assistant_input || 0,
      last_assistant_cache_read: data.last_assistant_cache_read || 0,
      last_turn_hit_rate: data.last_turn_hit_rate || 0,
      input_total: data.input_total || 0,
      model: data.model || null,
      source_mix: data.source_mix || null,
    };
    window._renderTokenBadge(wsTokenData, currentSessionId);
  }

  // Refresh the DAG context-range overlay — a turn just changed which
  // nodes the next message will carry as context.
  if (typeof window.refreshHistoryContextRange === 'function'
      && typeof currentSessionId !== 'undefined' && currentSessionId) {
    window.refreshHistoryContextRange(currentSessionId);
  }

  if (!el) return;
  var provider = data.provider || '';
  var result = _buildUsageText(chat, provider);
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

// Distill a tool's raw JSON args into a one-glance label.
//   * file_path / path → trimmed to a path relative to $HOME or repo root
//   * command          → just the command string
//   * pattern / query  → the search string
//   * fallback         → first 60 chars of raw JSON
// Keeps the chat narrow: full args still available in the unfolded body.
// Render a single stream event inside the current assistant bubble for a
// plain chat (no runtime_pending block). Supports three event types:
//   text      — streamed reply, rendered as markdown in .chat-text
//   thinking  — reasoning tokens, folded into a collapsible .chat-thinking
//   tool_use  — tool call, shown as a collapsible .chat-tool
//   tool_result — result for a prior tool_use, filled into matching .chat-tool
// Rebuild the streamed scaffold HTML from persisted blocks. Used when
// reloading a conversation — the live DOM is gone but msg.blocks has
// everything needed to regenerate the same collapsible layout.
// Toggle the outer chat-tools card. Header click target is the
// header div itself, so parent is the .chat-tools element.
// Copy all tool rows in this card as a single JSON blob.
function _handleStreamEvent(data) {
  // Phase 3: streaming deltas (text / thinking / tool calls, and the
  // /run CLI terminal) are rendered by React — the chat-stream reducer
  // applies stream events to the message store. Legacy DOM renderer
  // retired — no-op so the dispatch path holds.
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
  // Phase 3: the live execution tree is rendered by the React
  // <ExecutionTree /> inside <RuntimeBlock /> now — the chat-stream
  // reducer stores `tree_update` payloads on the reply message. This
  // legacy DOM renderer is retired; kept as a no-op so the
  // `handleChatResponse` dispatch doesn't throw.
}

function _handleRetryResult(data) {
  // Phase 3: retry results will be applied through the chat-stream
  // reducer in a later slice. Legacy DOM renderer retired — just clear
  // the running flag + refresh badges.
  setRunning(false);
  loadAgentSettings();
}

function _handleRuntimeResult(data, type) {
  // Phase 3: the runtime block is rendered by React <RuntimeBlock />
  // now (fed by the chat-stream reducer's `finalize`). This legacy DOM
  // renderer is retired — kept as a no-op so the dispatch path holds.
}

function _handleChatResult(data, type) {
  // Phase 3: plain-chat replies are rendered by React <AssistantBubble />
  // (chat-stream reducer). Legacy DOM renderer retired — no-op.
}
