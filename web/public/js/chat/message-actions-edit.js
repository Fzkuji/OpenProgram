// Inline-edit pencil for user messages. Click the pencil → the
// message bubble swaps into a textarea + Save/Cancel row. Save POSTs
// /api/chat/edit which forks a sibling user turn with the new content
// and re-runs it; the old version stays reachable via the < N/M >
// navigator (see message-actions-nav.js).
//
// Lives in its own file so message-actions.js stays focused on bar
// rendering. We publish a single entry point on window so the core
// bar module can call it without a build step.

(function () {
  if (window.__MESSAGE_ACTIONS_EDIT_WIRED__) return;
  window.__MESSAGE_ACTIONS_EDIT_WIRED__ = true;

  var PENCIL_SVG =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>';

  // Called by message-actions.js to append the Edit button on user
  // message bars. Kept here so the icon/handler live together.
  window.makeMessageEditButton = function (makeBtn) {
    return makeBtn('edit', 'Edit message', PENCIL_SVG);
  };

  function enterEditMode(messageEl) {
    // Idempotent — re-clicking pencil does nothing.
    if (messageEl.classList.contains('is-editing')) return;
    var contentEl = messageEl.querySelector('.message-content');
    if (!contentEl) return;

    // Preserve the original markdown-rendered innerHTML + the plain
    // text. We restore one or the other on Cancel vs Save.
    var originalHtml = contentEl.innerHTML;
    var originalText = contentEl.innerText || contentEl.textContent || '';

    messageEl.classList.add('is-editing');
    contentEl.innerHTML = '';

    var ta = document.createElement('textarea');
    ta.className = 'message-edit-textarea';
    ta.value = originalText;
    ta.rows = Math.max(2, Math.min(20, originalText.split('\n').length + 1));
    contentEl.appendChild(ta);

    var row = document.createElement('div');
    row.className = 'message-edit-actions';
    var cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'message-edit-btn message-edit-cancel';
    cancel.textContent = 'Cancel';
    var save = document.createElement('button');
    save.type = 'button';
    save.className = 'message-edit-btn message-edit-save';
    save.textContent = 'Save & resend';
    row.appendChild(cancel);
    row.appendChild(save);
    contentEl.appendChild(row);

    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);

    function leave() {
      messageEl.classList.remove('is-editing');
    }

    cancel.addEventListener('click', function () {
      contentEl.innerHTML = originalHtml;
      leave();
    });

    save.addEventListener('click', function () {
      var newContent = (ta.value || '').trim();
      if (!newContent) return;  // nothing to resubmit
      save.disabled = cancel.disabled = true;
      save.textContent = 'Submitting…';
      submitEdit(messageEl, newContent).catch(function (err) {
        // Restore editor so user can try again / cancel.
        save.disabled = cancel.disabled = false;
        save.textContent = 'Save & resend';
        console.error('[message-edit] submit failed:', err);
        alert('Edit failed: ' + (err && err.message || err));
      });
    });

    // Cmd/Ctrl+Enter = Save, Esc = Cancel.
    ta.addEventListener('keydown', function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        save.click();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        cancel.click();
      }
    });
  }

  function submitEdit(messageEl, newContent) {
    var sessionId = window.currentSessionId;
    var msgId = messageEl.getAttribute('data-msg-id');
    if (!sessionId || !msgId) {
      return Promise.reject(new Error('missing session_id or msg_id'));
    }
    return fetch('/api/chat/edit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, msg_id: msgId, content: newContent }),
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (e) { throw new Error(e.error || r.statusText); });
      return r.json();
    }).then(function () {
      // Flip run_active client-side — REST /api/chat/edit doesn't
      // emit chat_ack, so init.js wouldn't flip it. Terminal
      // chat_response types turn it back off.
      if (typeof window.setRunActive === 'function') window.setRunActive(true);
      // Re-request the conversation state so the client picks up the
      // new HEAD and sibling counts.
      if (window.ws && window.ws.readyState === WebSocket.OPEN) {
        window.ws.send(JSON.stringify({ action: 'load_session', session_id: sessionId }));
      }
    });
  }

  // Delegated click handler for any .message-action-btn[data-action="edit"].
  document.addEventListener('click', function (e) {
    var btn = e.target.closest ? e.target.closest('.message-action-btn[data-action="edit"]') : null;
    if (!btn) return;
    var messageEl = btn.closest('.message');
    if (!messageEl) return;
    enterEditMode(messageEl);
  }, true);
})();
