// < N / M > sibling-version navigator. Rendered beneath a message
// bubble when the server reports sibling_total > 1 for that turn (ie
// the user has retried or edited this turn one or more times).
//
// Click < / > → POST /api/chat/checkout with the prev/next sibling id
// → server moves HEAD → we re-request the conversation → UI re-renders
// the active branch. No execution happens; it's purely a display
// switch.

(function () {
  if (window.__MESSAGE_ACTIONS_NAV_WIRED__) return;
  window.__MESSAGE_ACTIONS_NAV_WIRED__ = true;

  var CHEVRON_LEFT =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<polyline points="15 18 9 12 15 6"/></svg>';
  var CHEVRON_RIGHT =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<polyline points="9 18 15 12 9 6"/></svg>';

  function makeNavBtn(dir, disabled) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'message-nav-btn';
    b.setAttribute('data-nav', dir);
    b.innerHTML = dir === 'prev' ? CHEVRON_LEFT : CHEVRON_RIGHT;
    b.disabled = !!disabled;
    b.setAttribute('aria-label',
      dir === 'prev' ? 'Previous version' : 'Next version');
    return b;
  }

  // Called by message-actions.js after ensureMessageActions. Appends
  // the `< N / M >` strip into the action bar itself so it shares the
  // bar's hover-gated visibility and sits on the same row. Idempotent.
  window.ensureSiblingNav = function (messageEl) {
    if (!messageEl) return;
    var idx = parseInt(messageEl.getAttribute('data-sibling-index') || '0', 10);
    var total = parseInt(messageEl.getAttribute('data-sibling-total') || '0', 10);
    // Bar now lives inside .message-header. Use a loose descendant
    // query so we find it regardless of nesting depth (header fallback
    // to messageEl when header is absent).
    var bar = messageEl.querySelector('.message-actions');
    if (!bar) return;
    var existing = bar.querySelector(':scope > .message-nav');

    if (total < 2) {
      if (existing) existing.remove();
      return;
    }

    if (existing) {
      existing.querySelector('.message-nav-label').textContent = idx + ' / ' + total;
      existing.querySelector('[data-nav="prev"]').disabled = idx <= 1;
      existing.querySelector('[data-nav="next"]').disabled = idx >= total;
      return;
    }

    var nav = document.createElement('div');
    nav.className = 'message-nav';
    nav.appendChild(makeNavBtn('prev', idx <= 1));
    var label = document.createElement('span');
    label.className = 'message-nav-label';
    label.textContent = idx + ' / ' + total;
    nav.appendChild(label);
    nav.appendChild(makeNavBtn('next', idx >= total));
    bar.appendChild(nav);
  };

  function resolveSiblingId(messageEl, dir) {
    // Server stamps prev/next sibling ids directly on each message
    // because the client only holds the linearized history under
    // HEAD — sibling branches aren't in _allMessages.
    var attr = dir === 'prev' ? 'data-prev-sibling' : 'data-next-sibling';
    return messageEl.getAttribute(attr) || null;
  }

  function checkout(targetId) {
    var sessionId = window.currentSessionId;
    if (!sessionId || !targetId) return Promise.reject(new Error('missing conv or target'));
    return fetch('/api/chat/checkout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, msg_id: targetId }),
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (e) { throw new Error(e.error || r.statusText); });
      return r.json();
    }).then(function () {
      // Ask the server for the fresh linear history under the new
      // HEAD. conversations.js handles the render.
      if (window.ws && window.ws.readyState === WebSocket.OPEN) {
        window.ws.send(JSON.stringify({ action: 'load_session', session_id: sessionId }));
      }
    });
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest ? e.target.closest('.message-nav-btn') : null;
    if (!btn || btn.disabled) return;
    var messageEl = btn.closest('.message');
    if (!messageEl) return;
    // When the user clicks "next" on an ASSISTANT message, they
    // really want to switch versions of the user turn above (the
    // assistant reply is a child of the user turn; siblings of the
    // assistant reply all parent to the same user turn, so they're
    // "different replies for the same question" — also valid, but
    // less common than "different questions"). We switch on whatever
    // the data attrs say — the server sets them with the right
    // granularity already.
    var dir = btn.getAttribute('data-nav');
    var targetId = resolveSiblingId(messageEl, dir);
    if (!targetId) return;
    btn.disabled = true;
    checkout(targetId).catch(function (err) {
      btn.disabled = false;
      console.error('[message-nav] checkout failed:', err);
    });
  }, true);
})();
