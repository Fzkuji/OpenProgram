// ===== Response Handling =====
//
// `chat_response` envelopes are rendered by the React message store —
// `useWS` calls the chat-stream reducer (`__applyChatWsMessage`) before
// this legacy handler runs. So `handleChatResponse` is now pure
// bookkeeping: token badge / branch cache / session-store feed / title.
// stream_event / tree_update / user_message / result-rendering all
// belong to the reducer; only `context_stats`, `status` and
// `follow_up_question` still have legacy-side work.

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
  if (type === 'follow_up_question') {
    _handleFollowUpQuestion(data);
    return;
  }
  // stream_event / tree_update / user_message are rendered by the
  // React reducer — nothing left to do legacy-side.
  if (type === 'stream_event' || type === 'tree_update' || type === 'user_message') {
    return;
  }

  // Final response (result / error / retry_result) -- task done
  setRunning(false);
  loadAgentSettings();
  // Refresh token counts: the assistant turn just persisted a new
  // provider_usage row, so the branch's current_tokens + the topbar
  // chip both need to re-read from the server.
  if (typeof window.refreshTokenBadge === 'function') {
    try { window.refreshTokenBadge(); } catch (e) {}
  }
  // The turn just appended new messages (and possibly created a new
  // branch tip). Force-refresh the branches cache so the right
  // sidebar visualization picks up new nodes without a session reload.
  if (typeof fetchBranches === 'function' && currentSessionId) {
    try {
      fetchBranches(currentSessionId, { force: true }).then(function () {
        if (typeof window._refreshBranchTokens === 'function') {
          try { window._refreshBranchTokens(); } catch (e) {}
        }
      });
    } catch (e) {}
  }

  // Tear down the elapsed-time ticker.
  if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }

  var isRuntimeResult = data.display === 'runtime' || (data.function && data.function !== 'chat');

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

function _handleContextStats(data) {
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
  // renders from.
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

  // Token badge — fed straight from WS data, no HTTP round-trip.
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
