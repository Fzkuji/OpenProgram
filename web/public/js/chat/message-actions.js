// Per-message Copy / Retry / Branch actions — attached to every
// .message bubble via a single delegated click handler on #chatMessages.
//
// Design: append the button row as a child of each `.message` so
// CSS `:hover` on the parent reveals the row. Injected at two points
// (1) when new bubbles are added (we call `ensureMessageActions(div)`
// from chat.js / conversations.js / chat-ws.js) and (2) lazily via a
// MutationObserver catching anything the other codepaths missed. The
// observer fallback means an action bar is guaranteed even if a new
// render path forgets to call the helper.
//
// msg_id wiring: bubbles carry the user-turn msg_id in
// `data-msg-id`. Retry / branch POST that to the REST endpoints. For
// freshly-sent user messages, chat.js stamps a temporary id and
// init.js swaps it for the server-assigned one when `chat_ack` comes
// in. Loaded-from-history bubbles get the real id straight from
// renderConversationMessages.
//
// Failure modes: bubble with no data-msg-id (rare race: assistant
// reply streamed before chat_ack — shouldn't happen but we guard).
// We disable retry/branch with a tooltip; copy still works because
// it only reads `.message-content` text.

(function () {
  if (window.__MESSAGE_ACTIONS_WIRED__) return;
  window.__MESSAGE_ACTIONS_WIRED__ = true;

  var ICON = {
    copy:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
    check:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<polyline points="20 6 9 17 4 12"/></svg>',
    retry:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>' +
      '<path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
    branch:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/>' +
      '<circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>',
  };

  function makeBtn(action, label, iconHtml) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'message-action-btn';
    b.setAttribute('data-action', action);
    b.title = label;
    b.setAttribute('aria-label', label);
    b.innerHTML = iconHtml;
    return b;
  }

  window.ensureMessageActions = function (messageEl) {
    if (!messageEl) return;
    // Skip system messages / runtime-only containers — retry on a
    // runtime block has its own existing UI, and system notes are
    // informational.
    if (messageEl.classList.contains('system')) return;

    var existing = messageEl.querySelector(':scope > .message-actions');
    if (existing) {
      // Bar exists but may have been knocked out of last-child
      // position by a later innerHTML update (chat-ws.js rebuilds
      // targetEl's body on 'result'). Keep it pinned to the bottom.
      if (existing !== messageEl.lastElementChild) {
        messageEl.appendChild(existing);
      }
      // Re-run nav — sibling counts may have changed since we last
      // rendered (a new retry just came in).
      if (typeof window.ensureSiblingNav === 'function') {
        window.ensureSiblingNav(messageEl);
      }
      return;
    }

    var isUser = messageEl.classList.contains('user');

    var bar = document.createElement('div');
    bar.className = 'message-actions';

    // Timestamp badge — sits at the left of the action bar, hoverable
    // for a full-date tooltip. Keeps the native tooltip off the whole
    // bubble (which would fire anywhere the mouse moved).
    var ts = messageEl.getAttribute('data-created-at');
    if (ts) {
      var tsMs = parseInt(ts, 10);
      if (tsMs && !isNaN(tsMs)) {
        var tsEl = document.createElement('span');
        tsEl.className = 'message-timestamp';
        try {
          var d = new Date(tsMs);
          tsEl.title = d.toLocaleString();
          tsEl.textContent = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch (e) { /* skip */ }
        bar.appendChild(tsEl);
      }
    }

    bar.appendChild(makeBtn('copy',  'Copy',            ICON.copy));
    bar.appendChild(makeBtn('retry', 'Retry from here', ICON.retry));
    // User messages get Edit (pencil) — fork the turn with new text.
    // See message-actions-edit.js. Assistant replies don't (you can't
    // edit the model's output and expect the edit to re-run).
    if (isUser && typeof window.makeMessageEditButton === 'function') {
      bar.appendChild(window.makeMessageEditButton(makeBtn));
    }
    // User messages don't get Branch — forking your own prompt into a
    // new conversation isn't a thing people do. Assistant replies do.
    if (!isUser) {
      bar.appendChild(makeBtn('branch', 'Branch into a new conversation', ICON.branch));
    }
    messageEl.appendChild(bar);

    // Sibling navigator (< N / M >) — rendered below the action bar
    // if the message has retry/edit siblings. See
    // message-actions-nav.js. Called after the bar is in the DOM so
    // it lands in the right position.
    if (typeof window.ensureSiblingNav === 'function') {
      window.ensureSiblingNav(messageEl);
    }
  };

  // -----------------------------------------------------------------
  // Action handlers
  // -----------------------------------------------------------------

  function extractContent(messageEl) {
    // Prefer the rendered .message-content (the markdown-ified text);
    // fall back to innerText of the bubble minus the action bar.
    var c = messageEl.querySelector('.message-content');
    if (c) return c.innerText || c.textContent || '';
    var clone = messageEl.cloneNode(true);
    var actions = clone.querySelector('.message-actions');
    if (actions) actions.remove();
    return clone.innerText || clone.textContent || '';
  }

  function flashCopied(btn) {
    var prev = btn.innerHTML;
    btn.classList.add('is-copied');
    btn.innerHTML = ICON.check;
    setTimeout(function () {
      btn.classList.remove('is-copied');
      btn.innerHTML = prev;
    }, 1200);
  }

  function doCopy(btn, messageEl) {
    var text = extractContent(messageEl);
    if (!text) return;
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text)
        .then(function () { flashCopied(btn); })
        .catch(function () { fallbackCopy(text); flashCopied(btn); });
    } else {
      fallbackCopy(text);
      flashCopied(btn);
    }
  }

  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } finally { ta.remove(); }
  }

  function doRetry(btn, messageEl) {
    var convId = window.currentConvId;
    var msgId = messageEl.getAttribute('data-msg-id');
    if (!convId || !msgId) {
      console.warn('[message-actions] retry: missing conv_id or msg_id', convId, msgId);
      return;
    }
    btn.disabled = true;

    // Non-destructive retry (ContextGit): server forks a sibling user
    // turn and runs it. We do NOT drop the DOM optimistically any
    // more — the old branch is still part of the DAG and has to stay
    // accessible via <N/M>. Instead we wait for the POST to succeed
    // then ask the server for the linearized view under the new HEAD.
    // load_conversation also carries sibling_index/_total so the
    // navigator shows up correctly.
    fetch('/api/chat/retry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conv_id: convId, msg_id: msgId }),
    })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { throw new Error(e.error || r.statusText); }); })
      .then(function () {
        // Mark the run as active client-side. The REST retry endpoint
        // doesn't emit a chat_ack, so init.js wouldn't know to set
        // this otherwise. Terminal chat_response types turn it off.
        if (typeof window.setRunActive === 'function') window.setRunActive(true);
        if (window.ws && window.ws.readyState === WebSocket.OPEN) {
          window.ws.send(JSON.stringify({ action: 'load_conversation', conv_id: convId }));
        }
      })
      .catch(function (err) {
        console.error('[message-actions] retry failed:', err);
        btn.disabled = false;
      });
  }

  function doBranch(btn, messageEl) {
    var convId = window.currentConvId;
    var msgId = messageEl.getAttribute('data-msg-id');
    if (!convId || !msgId) {
      console.warn('[message-actions] branch: missing conv_id or msg_id', convId, msgId);
      return;
    }
    btn.disabled = true;
    fetch('/api/chat/branch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conv_id: convId, msg_id: msgId }),
    })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { throw new Error(e.error || r.statusText); }); })
      .then(function (res) {
        // Navigate to the new conversation. The WS handler for
        // conversation_loaded will render the copied messages.
        var newId = res.conv_id;
        history.pushState(null, '', '/c/' + newId);
        window.currentConvId = newId;
        if (window.ws && window.ws.readyState === WebSocket.OPEN) {
          window.ws.send(JSON.stringify({ action: 'load_conversation', conv_id: newId }));
        } else {
          // Fallback: full reload onto the new URL.
          window.location.href = '/c/' + newId;
        }
      })
      .catch(function (err) {
        console.error('[message-actions] branch failed:', err);
        btn.disabled = false;
      });
  }

  // -----------------------------------------------------------------
  // Delegated click handler + MutationObserver for auto-attach
  // -----------------------------------------------------------------

  function onChatClick(e) {
    var btn = e.target.closest ? e.target.closest('.message-action-btn') : null;
    if (!btn) return;
    var messageEl = btn.closest('.message');
    if (!messageEl) return;
    var action = btn.getAttribute('data-action');
    if (action === 'copy')   return doCopy(btn, messageEl);
    if (action === 'retry')  return doRetry(btn, messageEl);
    if (action === 'branch') return doBranch(btn, messageEl);
  }

  function attachObserver() {
    var container = document.getElementById('chatMessages');
    if (!container) return;
    container.addEventListener('click', onChatClick, true);

    // Attach action bars to anything already rendered, plus future
    // additions. Idempotent: ensureMessageActions is a no-op if the
    // bar is already there.
    container.querySelectorAll('.message').forEach(window.ensureMessageActions);
    var obs = new MutationObserver(function (muts) {
      // Two jobs: (1) attach action bars to freshly added .message
      // nodes; (2) whenever any mutation happens inside an existing
      // .message, re-pin its action bar to the last-child position
      // — streaming deltas keep inserting new content blocks and
      // would otherwise shove the bar into the middle.
      var touched = new Set();
      for (var i = 0; i < muts.length; i++) {
        var m = muts[i];
        for (var j = 0; j < m.addedNodes.length; j++) {
          var n = m.addedNodes[j];
          if (n.nodeType !== 1) continue;
          if (n.classList && n.classList.contains('message')) {
            window.ensureMessageActions(n);
          } else if (n.querySelectorAll) {
            n.querySelectorAll('.message').forEach(window.ensureMessageActions);
          }
        }
        // Find the enclosing .message (if any) for the mutation
        // target; mark it so we re-pin its actions below.
        var t = m.target;
        while (t && t !== container) {
          if (t.nodeType === 1 && t.classList && t.classList.contains('message')) {
            touched.add(t);
            break;
          }
          t = t.parentNode;
        }
      }
      touched.forEach(window.ensureMessageActions);
    });
    obs.observe(container, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attachObserver);
  } else {
    attachObserver();
  }
})();
