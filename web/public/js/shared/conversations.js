function _channelLabel(channel, accountId) {
  if (!channel) return 'local';
  return accountId ? channel + ':' + accountId : channel;
}

// Map a channel platform id to a brand-icon URL on simple-icons'
// public CDN. simple-icons ships official-mark SVGs for hundreds of
// platforms under an open license, intended exactly for "your app
// integrates with X" indicators. Each URL embeds the brand's own
// primary color so the icons read like the real platform — WeChat
// shows as its #07C160 green, Discord as its blurple, etc. Falls
// back to a single-letter chip if the icon fails to load.
var _CHANNEL_ICON_URL = {
  wechat:   'https://cdn.simpleicons.org/wechat/07C160',
  discord:  'https://cdn.simpleicons.org/discord/5865F2',
  telegram: 'https://cdn.simpleicons.org/telegram/26A5E4',
  slack:    'https://cdn.simpleicons.org/slack/4A154B',
};

// Channel health poller. When the active conv binds to a channel
// (wechat / discord / telegram / slack), poll the backend heartbeat
// endpoint every 5s and toggle the status-badge dot via
// `setStatusDotHealth(state)`. The badge *text* keeps showing
// "WeChat (xxx) · …" — only the dot reflects liveness.
//
// Backend semantics (see openprogram/webui/routes/channels.py):
//   alive=true            → adapter thread heartbeated within 30s → green
//   alive=false, unknown  → never seen (not started yet)          → yellow
//   alive=false, stale    → was alive, heartbeat went silent      → red
var _channelHealthTimer = null;
var _channelHealthKey = null;

function _stopChannelHealthPoll() {
  if (_channelHealthTimer) {
    clearInterval(_channelHealthTimer);
    _channelHealthTimer = null;
  }
  _channelHealthKey = null;
}
window._stopChannelHealthPoll = _stopChannelHealthPoll;

function _startChannelHealthPoll(channel, account_id) {
  var key = channel + ':' + (account_id || 'default');
  if (_channelHealthKey === key) return;  // already polling this one
  _stopChannelHealthPoll();
  _channelHealthKey = key;

  function _probe() {
    if (_channelHealthKey !== key) return;
    var url = '/api/channels/' + encodeURIComponent(channel)
            + '/' + encodeURIComponent(account_id || 'default') + '/status';
    fetch(url, { cache: 'no-store' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (_channelHealthKey !== key) return;
        if (typeof window.setStatusDotHealth !== 'function') return;
        var state = 'err';
        if (data.alive) state = 'ok';
        else if (data.state === 'unknown') state = 'warn';
        window.setStatusDotHealth(state);
      })
      .catch(function() {
        if (_channelHealthKey !== key) return;
        if (typeof window.setStatusDotHealth === 'function') {
          window.setStatusDotHealth('err');
        }
      });
  }
  _probe();
  _channelHealthTimer = setInterval(_probe, 5000);
}
window._startChannelHealthPoll = _startChannelHealthPoll;

function _channelIcon(plat) {
  var lc = String(plat || '').toLowerCase();
  var url = _CHANNEL_ICON_URL[lc];
  var letter = ((plat || '?')[0] || '?').toUpperCase();
  // The fallback letter chip is also what dropdown providers use, so
  // a broken icon still looks intentional rather than empty.
  var letterSpan = '<span class="provider-icon-letter">' + letter + '</span>';
  if (!url) return letterSpan;
  return '<img src="' + url + '" alt="" '
       + 'onerror="this.outerHTML=&quot;' + letterSpan.replace(/"/g, '&amp;quot;') + '&quot;">';
}
window._channelIcon = _channelIcon;

function renderSessions() {
  // React owns this rendering now (components/sidebar/sessions-list.tsx).
  // Early return so legacy callers (WS sessions_list handler, etc.)
  // don't fight the React reconciler by overwriting #convList with
  // innerHTML strings.
  return;
}
function _legacyRenderSessions_deprecated() {
  var container = document.getElementById('convList');
  var html = '';
  var convs = Object.values(conversations).sort(function(a, b) { return (b.created_at || 0) - (a.created_at || 0); });
  if (convs.length === 0) {
    html += '<div style="padding:8px 16px;font-size:12px;color:var(--text-muted)">No conversations yet</div>';
  } else {
    for (var ci = 0; ci < convs.length; ci++) {
      var c = convs[ci];
      var active = c.id === currentSessionId ? ' active' : '';
      // Build a clean display label: "<channel> (<account>) · <title>"
      // when the conv is bound to a channel; otherwise just the title.
      // Strip backend placeholder titles ("WeChat: o9cq..." etc.) so
      // the raw account id doesn't leak into the list.
      var prefix = (typeof window._channelPrefixFor === 'function') ?
                   window._channelPrefixFor(c.channel, c.account_id) : '';
      var realTitle = (typeof window._displayTitleFor === 'function') ?
                      window._displayTitleFor(c) : (c.title || '');
      // When the title is a backend placeholder, fall back to a
      // preview of the most recent user message so the user keeps
      // seeing some content. Pulled in from the server snapshot.
      if (!realTitle && c.preview) {
        var pv = String(c.preview).trim();
        realTitle = pv.length > 30 ? pv.slice(0, 30) + '…' : pv;
      }
      var label;
      if (prefix && realTitle)      label = prefix + ': ' + realTitle;
      else if (prefix)              label = prefix;
      else if (realTitle)           label = realTitle;
      else                          label = c.title || 'Untitled';
      html += '<div class="conv-item' + active + '" onclick="switchSession(\'' + c.id + '\')" title="' + escAttr(label) + '">' +
        '<span class="conv-title">' + escHtml(label) + '</span>' +
        '<span class="conv-del" onclick="event.stopPropagation();deleteSession(\'' + c.id + '\')" title="Delete"><svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="2" y1="2" x2="8" y2="8"/><line x1="8" y1="2" x2="2" y2="8"/></svg></span>' +
      '</div>';
    }
    html += '<div class="conv-clear-all" onclick="clearAllSessions()">Clear all</div>';
  }
  container.innerHTML = html;
}

// Cached list of channel accounts (filled lazily). Each entry:
// { channel, account_id, name, enabled, configured }.
var _channelAccountsCache = null;
var _channelAccountsPending = null;  // resolve fn for in-flight fetch

function fetchChannelAccounts() {
  if (_channelAccountsCache) return Promise.resolve(_channelAccountsCache);
  if (_channelAccountsPending) {
    return new Promise(function(res) {
      var prev = _channelAccountsPending;
      _channelAccountsPending = function(v) { prev(v); res(v); };
    });
  }
  return new Promise(function(res) {
    _channelAccountsPending = res;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'list_channel_accounts' }));
    } else {
      _channelAccountsPending = null;
      res([]);
    }
    setTimeout(function() {
      if (_channelAccountsPending === res) {
        _channelAccountsPending = null;
        res(_channelAccountsCache || []);
      }
    }, 3000);
  });
}

// Called by ws message handler when a channel_accounts envelope arrives.
function _onChannelAccountsMessage(rows) {
  _channelAccountsCache = Array.isArray(rows) ? rows : [];
  if (_channelAccountsPending) {
    var fn = _channelAccountsPending;
    _channelAccountsPending = null;
    fn(_channelAccountsCache);
  }
}
window._onChannelAccountsMessage = _onChannelAccountsMessage;

function _currentChannelChoice() {
  // For an existing conv, the badge reflects that conv's channel.
  // For a brand-new conv (no currentSessionId yet), it reflects the
  // pending choice that will be sent with the first message.
  if (currentSessionId && conversations[currentSessionId]) {
    var c = conversations[currentSessionId];
    return { channel: c.channel || null, account_id: c.account_id || null };
  }
  return window._pendingChannelChoice || { channel: null, account_id: null };
}

window.refreshChannelBadge = function() {
  // Channel state is shown by the existing #statusBadge via
  // refreshStatusSource; this hook just delegates so callers don't
  // need to know which renderer owns it.
  if (typeof window.refreshStatusSource === 'function') {
    window.refreshStatusSource();
  }
};

function openChannelDropdown(evt) {
  if (evt) evt.stopPropagation();
  // Toggle close if our dropdown is already open.
  if (document.getElementById('channelDropdown')) { _closeChannelDropdown(); return; }
  // Mutual exclusivity with other topbar popovers (chat / exec / model
  // selectors etc.) — same coordinator the agent selectors use.
  if (window._closeAllPopovers) window._closeAllPopovers('channel');
  var badge = document.getElementById('statusBadge');
  if (!badge) return;
  var sessionId = currentSessionId || null;
  var cur = _currentChannelChoice();

  fetchChannelAccounts().then(function(rows) {
    var enabled = (rows || []).filter(function(r) { return r.enabled; });
    var rect = badge.getBoundingClientRect();
    var dd = document.createElement('div');
    // Reuse the model-dropdown styling so chat / exec / channel pickers
    // all look the same. Adds .channel-selector for any channel-only
    // overrides we might want later.
    dd.className = 'agent-selector model-dropdown channel-selector';
    dd.id = 'channelDropdown';
    dd.style.top = (rect.bottom + 4) + 'px';
    dd.style.left = rect.left + 'px';

    var brandFor = function(plat) {
      var map = { wechat: 'WeChat', discord: 'Discord', telegram: 'Telegram', slack: 'Slack' };
      return map[String(plat).toLowerCase()] || plat;
    };

    var html = '';
    html += '<div class="model-dd-group-label" style="padding-top:6px"><span>Conversation channel</span></div>';

    // "Local" sits at the top as its own row — no channel binding.
    html += '<div class="model-dd-item' + (!cur.channel ? ' active' : '') + '" data-ch="" data-acct="">' +
              '<span class="model-dd-name">Local</span>' +
            '</div>';

    if (enabled.length === 0) {
      html += '<div class="model-dd-group-label" style="padding-top:10px;font-size:11px">' +
                '<a href="/settings" style="color:var(--accent-blue);text-decoration:none">Add a channel in Settings →</a>' +
              '</div>';
    } else {
      // Group accounts by platform: a group label row (icon + brand
      // name) and then one row per account under it. Same shape as
      // the chat / exec dropdowns.
      var byPlat = {};
      var order = [];
      enabled.forEach(function(r) {
        if (!byPlat[r.channel]) { byPlat[r.channel] = []; order.push(r.channel); }
        byPlat[r.channel].push(r);
      });
      order.forEach(function(plat) {
        html += '<div class="model-dd-group-label">' +
                  '<span class="provider-icon" style="width:14px;height:14px">' +
                    _channelIcon(plat) +
                  '</span>' +
                  '<span>' + escHtml(brandFor(plat)) + '</span>' +
                '</div>';
        byPlat[plat].forEach(function(r) {
          var active = (r.channel === cur.channel && r.account_id === cur.account_id);
          var meta = r.name && r.name !== r.account_id ? r.name : '';
          html += '<div class="model-dd-item' + (active ? ' active' : '') + '"' +
                    ' data-ch="' + escAttr(r.channel) + '"' +
                    ' data-acct="' + escAttr(r.account_id) + '">' +
                    '<span class="model-dd-name">' + escHtml(r.account_id) + '</span>' +
                    (meta ? '<span class="model-dd-caps"><span class="cap-badge ctx">' + escHtml(meta) + '</span></span>' : '') +
                  '</div>';
        });
      });
    }

    dd.innerHTML = html;
    document.body.appendChild(dd);

    dd.addEventListener('click', function(e) {
      var item = e.target.closest('[data-ch]');
      if (!item) return;
      e.stopPropagation();
      var ch = item.getAttribute('data-ch') || '';
      var acct = item.getAttribute('data-acct') || '';
      if (sessionId) {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ action: 'set_conversation_channel',
            session_id: sessionId, channel: ch, account_id: acct }));
        }
        var conv = conversations[sessionId];
        if (conv) {
          conv.channel = ch || null;
          conv.account_id = (ch && acct) ? acct : null;
        }
      } else {
        window._pendingChannelChoice = { channel: ch || null, account_id: ch ? (acct || null) : null };
      }
      window.refreshChannelBadge();
      _closeChannelDropdown();
    });

    // Close on outside click — defer the listener so the click that
    // opened the dropdown doesn't immediately close it.
    setTimeout(function() {
      document.addEventListener('click', _channelDropdownDocClick, { once: true });
    }, 0);
  });
}
window.openChannelDropdown = openChannelDropdown;

function _channelDropdownDocClick(e) {
  var dd = document.getElementById('channelDropdown');
  if (dd && dd.contains(e.target)) {
    document.addEventListener('click', _channelDropdownDocClick, { once: true });
    return;
  }
  _closeChannelDropdown();
}

function _closeChannelDropdown() {
  var dd = document.getElementById('channelDropdown');
  if (dd) dd.remove();
  document.removeEventListener('click', _channelDropdownDocClick);
}

// ===== Branch (git-style) selector ============================
//
// Each leaf message in a session's DAG is a "branch tip". The
// session.head_id is the currently-checked-out branch. We expose:
//   - list_branches  → branches_list      (cached, refreshed lazily)
//   - checkout_branch → branch_checked_out (sets head_id)
//   - rename_branch / delete_branch_name (TODO UI)

var _branchesByConv = {};   // session_id → [{head_msg_id, name, active, ...}]
var _branchesPending = {};  // session_id → resolve fn

function fetchBranches(sessionId, opts) {
  if (!sessionId) return Promise.resolve([]);
  var force = !!(opts && opts.force);
  if (force) delete _branchesByConv[sessionId];
  if (_branchesByConv[sessionId]) return Promise.resolve(_branchesByConv[sessionId]);
  if (_branchesPending[sessionId]) {
    return new Promise(function(res) {
      var prev = _branchesPending[sessionId];
      _branchesPending[sessionId] = function(v) { prev(v); res(v); };
    });
  }
  return new Promise(function(res) {
    _branchesPending[sessionId] = res;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'list_branches', session_id: sessionId }));
    } else {
      delete _branchesPending[sessionId];
      res([]);
    }
    setTimeout(function() {
      if (_branchesPending[sessionId] === res) {
        delete _branchesPending[sessionId];
        res(_branchesByConv[sessionId] || []);
      }
    }, 3000);
  });
}

function _onBranchesListMessage(payload) {
  if (!payload || !payload.session_id) return;
  var rows = Array.isArray(payload.branches) ? payload.branches : [];
  _branchesByConv[payload.session_id] = rows;
  if (_branchesPending[payload.session_id]) {
    var fn = _branchesPending[payload.session_id];
    delete _branchesPending[payload.session_id];
    fn(rows);
  }
  if (payload.session_id === currentSessionId) {
    if (typeof window.refreshBranchBadge === 'function') window.refreshBranchBadge();
    if (typeof window.repaintBranchTags === 'function') window.repaintBranchTags();
    if (typeof window.renderBranchesPanel === 'function') window.renderBranchesPanel();
    // History DAG visualization (right rail): re-render whenever the
    // branches payload carries a fresh graph snapshot. This lets nodes
    // appear in real time the moment a user message (or assistant
    // reply) lands in the DB, without waiting for the next
    // load_session round-trip.
    if (Array.isArray(payload.graph) && typeof window.renderHistoryGraph === 'function') {
      try { window.renderHistoryGraph(payload.graph, payload.active || null); } catch (e) {}
      // Keep the in-memory conversation snapshot in sync too.
      if (conversations[payload.session_id]) {
        conversations[payload.session_id].graph = payload.graph;
        if (payload.active) conversations[payload.session_id].head_id = payload.active;
      }
    }
  }
}

// Right-sidebar Branches panel — third entry point for switching
// branches (besides the topbar chip dropdown and clicking a node in
// the history graph). Renders the same list with a collapsed/expanded
// toggle: collapsed shows just the active branch as a chip; expanded
// shows the whole list.
// Per-branch token usage cache. Keyed by session, then head_msg_id.
// Populated by _refreshBranchTokens off the batch endpoint, consumed
// by renderBranchesPanel to paint a "12K (6%)" suffix on each row.
var _branchTokensByConv = {};

function _formatBranchTokens(n) {
  if (!n) return '';
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000)    return (n / 1000).toFixed(1) + 'K';
  return String(n);
}

async function _refreshBranchTokens() {
  if (!currentSessionId) return;
  try {
    var r = await fetch('/api/sessions/' + encodeURIComponent(currentSessionId) + '/branches/tokens');
    if (!r.ok) return;
    var d = await r.json();
    var map = {};
    (d.branches || []).forEach(function (b) { map[b.head_id] = b; });
    _branchTokensByConv[currentSessionId] = map;
    if (typeof window.renderBranchesPanel === 'function') window.renderBranchesPanel();
  } catch (e) {}
}
window._refreshBranchTokens = _refreshBranchTokens;

// Inline-rename for a branch row — used by both the right-dock
// panel (renderBranchesPanel) and (eventually) the topbar dropdown.
// Replaces `nameEl`'s text with a focused <input>; Enter/blur commit,
// Esc/empty cancel. Empty submit is treated as cancel (consistent
// with the dropdown's behavior after the recent fix), not as an
// "AI auto-name" request.
function _inlineRenameBranchRow(nameEl, headMsgId, currentName) {
  if (!nameEl || nameEl.dataset.editing === '1') return;
  nameEl.dataset.editing = '1';
  var originalText = nameEl.textContent;
  var current = currentName || '';
  var inp = document.createElement('input');
  inp.type = 'text';
  inp.value = current;
  inp.placeholder = 'new branch name (empty = cancel)';
  inp.style.width = '100%';
  inp.style.boxSizing = 'border-box';
  inp.style.font = 'inherit';
  inp.style.color = 'var(--text-bright)';
  inp.style.background = 'var(--bg-input, rgba(255,255,255,0.06))';
  inp.style.border = '1px solid var(--accent-blue, #6cb4ff)';
  inp.style.borderRadius = '4px';
  inp.style.padding = '2px 6px';
  inp.style.outline = 'none';
  nameEl.textContent = '';
  nameEl.appendChild(inp);
  setTimeout(function () { inp.focus(); inp.select(); }, 0);
  var submitted = false;
  function commit(value) {
    if (submitted) return;
    submitted = true;
    var trimmed = (value || '').trim();
    if (trimmed && trimmed !== current
        && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        action: 'rename_branch',
        session_id: currentSessionId,
        head_msg_id: headMsgId,
        name: trimmed,
      }));
    }
    delete nameEl.dataset.editing;
    nameEl.textContent = originalText;
  }
  function cancel() {
    if (submitted) return;
    submitted = true;
    delete nameEl.dataset.editing;
    nameEl.textContent = originalText;
  }
  inp.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter') { ev.preventDefault(); commit(inp.value); }
    else if (ev.key === 'Escape') { ev.preventDefault(); cancel(); }
  });
  inp.addEventListener('blur', function () { commit(inp.value); });
  inp.addEventListener('click', function (ev) { ev.stopPropagation(); });
}

window.renderBranchesPanel = function () {
  var host = document.getElementById('branchesPanel');
  if (!host) return;
  if (!currentSessionId) { host.innerHTML = ''; host.className = ''; return; }
  var rows = _branchesByConv[currentSessionId] || [];
  if (!rows.length) { host.innerHTML = ''; host.className = ''; return; }
  var tokenMap = _branchTokensByConv[currentSessionId] || {};
  if (!Object.keys(tokenMap).length) {
    // First paint without data; kick off the fetch which will re-render.
    _refreshBranchTokens();
  }
  // Preserve the user's scroll position across re-renders. innerHTML
  // rewrite below tears down the .branches-list element, so the
  // browser resets scrollTop to 0 — exactly the "panel jumps" the
  // user is complaining about. Capture the value before the rewrite
  // and restore it after.
  var _prevScrollTop = 0;
  var _prevList = host.querySelector('.branches-list');
  if (_prevList) _prevScrollTop = _prevList.scrollTop;

  // Always start collapsed when the page loads / conversation changes.
  // Persist expand state only in-memory for this single render cycle —
  // the user explicitly wants "collapsed by default, expand on click".
  if (typeof window._branchesPanelCollapsed === 'undefined') {
    window._branchesPanelCollapsed = true;
  }
  var collapsed = window._branchesPanelCollapsed !== false;

  // Use lane colors from the history graph if available; fallback to index order.
  var _branchLaneColors = [
    '#4f8ef7','#5aad4e','#d4843a','#9d6fe0','#e0445a',
    '#2db3d5','#d96d2d','#35b89a','#6b8dd6','#2ec4b6'
  ];
  var graphColorMap = window._branchLaneColorMap || {};
  var colorMap = {};
  rows.forEach(function (b, idx) {
    colorMap[b.head_msg_id] = graphColorMap[b.head_msg_id] || _branchLaneColors[idx % _branchLaneColors.length];
  });

  // Branches render in their natural DAG order; the active one stays in
  // place (no float-to-top). Collapsed mode hides every row except the
  // active one — so the panel always shows where HEAD is, regardless of
  // expanded state.

  host.className = 'branches-section' + (collapsed ? ' is-collapsed' : '');
  host.innerHTML =
    '<div class="sidebar-section-header">' +
      '<span class="sidebar-section-title">Branches</span>' +
      '<span class="sidebar-section-hint">' + (collapsed ? 'Show' : 'Hide') + '</span>' +
    '</div>' +
    '<div class="branches-list"></div>';

  host.querySelector('.sidebar-section-header').addEventListener('click', function () {
    var wasCollapsed = window._branchesPanelCollapsed !== false;
    window._branchesPanelCollapsed = !wasCollapsed;
    // Going from collapsed → expanded: position HEAD into view.
    if (wasCollapsed) window._branchesNextScrollToHead = true;
    window.renderBranchesPanel();
  });

  var list = host.querySelector('.branches-list');

  rows.forEach(function (b) {
    var laneColor = colorMap[b.head_msg_id] || _branchLaneColors[0];
    var item = document.createElement('div');
    item.className = 'branch-item' + (b.active ? ' active' : '');
    // Collapsed mode: hide every non-active row in JS so the natural
    // order is preserved when re-expanded. Avoids CSS :nth-child games.
    if (collapsed && !b.active) item.style.display = 'none';
    item.setAttribute('data-head', b.head_msg_id);
    // Hover-revealed rename + delete buttons live to the right of
    // the name (mirrors the topbar branch-dropdown). The HEAD badge,
    // when present, sits before them so the action buttons always
    // anchor flush-right.
    // Match the topbar branch-dropdown's exact SVGs (size, viewBox,
    // stroke-width, path) so both surfaces render identical icons.
    var renameSvg = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11.5 2.5l2 2L5 13l-3 1 1-3 8.5-8.5z"/></svg>';
    var delSvg = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="2" y1="2" x2="8" y2="8"/><line x1="8" y1="2" x2="2" y2="8"/></svg>';
    item.innerHTML =
      '<span class="branch-item-dot" style="background:' + laneColor + '"></span>' +
      '<span class="branch-item-name">' + escHtml(b.name) + '</span>' +
      (b.active ? '<span class="branch-item-badge">HEAD</span>' : '') +
      '<span class="branch-item-actions">' +
        '<span class="branch-item-action branch-item-rename" title="Rename branch" data-rename="' + escAttr(b.head_msg_id) + '">' + renameSvg + '</span>' +
        '<span class="branch-item-action branch-item-del" title="Delete branch" data-del="' + escAttr(b.head_msg_id) + '">' + delSvg + '</span>' +
      '</span>';

    var nameEl = item.querySelector('.branch-item-name');
    var renameBtn = item.querySelector('.branch-item-rename');
    var delBtn = item.querySelector('.branch-item-del');

    if (renameBtn) {
      renameBtn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        // If the topbar branch-dropdown is open, close it — otherwise
        // it shows a stale name after the rename round-trips, and the
        // two surfaces feel like they're fighting over who owns the
        // current state.
        if (typeof _closeBranchDropdown === 'function') _closeBranchDropdown();
        _inlineRenameBranchRow(nameEl, b.head_msg_id, b.name || '');
      });
    }
    if (delBtn) {
      delBtn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        if (!confirm('Delete this branch and its messages? This cannot be undone.')) return;
        // Same reasoning as rename: the topbar dropdown would
        // otherwise still list the now-deleted branch.
        if (typeof _closeBranchDropdown === 'function') _closeBranchDropdown();
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            action: 'delete_branch',
            session_id: currentSessionId,
            head_msg_id: b.head_msg_id,
          }));
          ws.send(JSON.stringify({
            action: 'load_session',
            session_id: currentSessionId,
          }));
        }
      });
    }
    item.addEventListener('click', function (ev) {
      // Don't fire checkout if the click landed inside the inline
      // rename input or on either action button — those have their
      // own handlers and stopPropagation.
      if (nameEl && nameEl.dataset.editing === '1') return;
      if (b.active) return;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: 'checkout_branch', session_id: currentSessionId, head_msg_id: b.head_msg_id }));
        ws.send(JSON.stringify({ action: 'load_session', session_id: currentSessionId }));
      }
    });
    list.appendChild(item);
  });

  // Restore the user's scroll position. Do NOT compute / adjust based
  // on the active branch — the user explicitly asked for zero
  // self-motion on checkout. Any auto-scroll into view, even
  // block:'nearest', registers as a jump.
  list.scrollTop = _prevScrollTop;

  // Exception: an explicit collapsed→expanded toggle wants HEAD in
  // the visible window. Honour exactly once and clear the flag so
  // the next checkout doesn't move the panel.
  if (window._branchesNextScrollToHead) {
    window._branchesNextScrollToHead = false;
    var activeEl = list.querySelector('.branch-item.active');
    if (activeEl) {
      var aTop = activeEl.offsetTop;
      var aH   = activeEl.offsetHeight;
      var cH   = list.clientHeight;
      if (aTop < list.scrollTop) {
        list.scrollTop = aTop;
      } else if (aTop + aH > list.scrollTop + cH) {
        list.scrollTop = aTop + aH - cH;
      }
    }
  }
};
window._onBranchesListMessage = _onBranchesListMessage;

function _onBranchCheckedOut(payload) {
  if (!payload || !payload.ok || !payload.session_id) return;
  // Invalidate cache so the next dropdown re-fetches with the new
  // active marker. The server-side history graph / message list will
  // update through their own existing envelopes.
  delete _branchesByConv[payload.session_id];
  if (payload.session_id === currentSessionId && typeof window.refreshBranchBadge === 'function') {
    fetchBranches(payload.session_id).then(window.refreshBranchBadge);
  }
}
window._onBranchCheckedOut = _onBranchCheckedOut;

window.refreshBranchBadge = function() {
  var badge = document.getElementById('branchBadge');
  if (!badge) return;
  if (!currentSessionId) { badge.style.display = 'none'; return; }
  var list = _branchesByConv[currentSessionId] || [];
  // Show the chip even with a single branch — gives a stable place to
  // see the current branch name and (eventually) rename / split it.
  // Hidden only when the session has no branches at all (empty conv).
  if (list.length === 0) {
    badge.style.display = 'none';
    return;
  }
  var active = list.find(function(b) { return b.active; });
  var label = active ? active.name : 'detached';
  var nameEl = badge.querySelector('.branch-name');
  if (nameEl) {
    nameEl.textContent = label + ' (' + list.length + ')';
    // Cap width + ellipsis so a long auto-name doesn't blow up topbar.
    nameEl.style.display = 'inline-block';
    nameEl.style.maxWidth = '180px';
    nameEl.style.overflow = 'hidden';
    nameEl.style.textOverflow = 'ellipsis';
    nameEl.style.whiteSpace = 'nowrap';
    nameEl.style.verticalAlign = 'bottom';
  }
  badge.title = label + ' (' + list.length + ' branches)';
  badge.style.display = '';
};

function openBranchDropdown(evt) {
  if (evt) evt.stopPropagation();
  if (document.getElementById('branchDropdown')) { _closeBranchDropdown(); return; }
  if (window._closeAllPopovers) window._closeAllPopovers('branch');
  var badge = document.getElementById('branchBadge');
  if (!badge || !currentSessionId) return;

  // Force-refresh on open so the active flag and any new leaves from
  // recent retries / edits are picked up.
  delete _branchesByConv[currentSessionId];
  fetchBranches(currentSessionId).then(function(rows) {
    var rect = badge.getBoundingClientRect();
    var dd = document.createElement('div');
    dd.id = 'branchDropdown';
    dd.className = 'agent-selector model-dropdown branch-selector';
    dd.style.top = (rect.bottom + 4) + 'px';
    dd.style.left = rect.left + 'px';

    var html = '<div class="model-dd-group-label" style="padding-top:6px"><span>Branches</span></div>';
    if (!rows.length) {
      html += '<div class="model-dd-group-label" style="font-size:11px"><span>No branches yet — retry or edit a message to fork.</span></div>';
    } else {
      rows.forEach(function(b) {
        html += '<div class="model-dd-item' + (b.active ? ' active' : '') + '"' +
                  ' data-head="' + escAttr(b.head_msg_id) + '">' +
                  '<span class="model-dd-name">' + escHtml(b.name) + '</span>' +
                  '<span class="branch-rename" data-rename="' + escAttr(b.head_msg_id) + '"' +
                    ' data-current="' + escAttr(b.is_named ? b.name : '') + '"' +
                    ' title="Rename (empty = AI auto-name)">' +
                    '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11.5 2.5l2 2L5 13l-3 1 1-3 8.5-8.5z"/></svg>' +
                  '</span>' +
                  (b.active ? '<span class="cap-badge ctx branch-head">HEAD</span>' : '') +
                  '<span class="branch-del" data-del="' + escAttr(b.head_msg_id) + '" title="Delete this branch">' +
                    '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="2" y1="2" x2="8" y2="8"/><line x1="8" y1="2" x2="2" y2="8"/></svg>' +
                  '</span>' +
                '</div>';
      });
    }
    dd.innerHTML = html;
    document.body.appendChild(dd);

    // Force layout via inline styles so the dropdown sizes purely to
    // content + the chip/X share the same right-anchored slot. CSS
    // specificity from the legacy .model-dropdown rules was making
    // the spec-by-class approach unreliable; inline always wins.
    dd.style.minWidth = '0';
    dd.style.maxWidth = 'none';
    dd.style.width = 'auto';
    dd.style.boxShadow = '0 12px 32px rgba(0, 0, 0, 0.5)';
    dd.querySelectorAll('.model-dd-item').forEach(function (row) {
      row.style.gap = '0';
      row.style.position = 'relative';
      // Reserve right side for HEAD chip + X (right-anchored absolute).
      row.style.paddingRight = '64px';

      var nm = row.querySelector('.model-dd-name');
      if (nm) {
        nm.style.flex = '1 1 auto';
        nm.style.minWidth = '0';
        // Cap the name's render width so a long auto-name (last
        // assistant reply) doesn't stretch the dropdown beyond the
        // viewport — global ellipsis CSS truncates the rest.
        nm.style.maxWidth = '320px';
      }
      // HEAD chip — anchored to row's right edge.
      var head = row.querySelector('.branch-head');
      if (head) {
        head.style.position = 'absolute';
        head.style.right = '8px';
        head.style.top = '50%';
        head.style.transform = 'translateY(-50%)';
        head.style.pointerEvents = 'none';
        head.style.padding = '0 8px';
        head.style.height = '20px';
        head.style.lineHeight = '20px';
        head.style.display = 'inline-flex';
        head.style.alignItems = 'center';
      }
      // Both buttons are 24×24 (matches sidebar's .conv-del visual
      // weight better) and sit 8px apart so the icons feel like one
      // tight group. Default: transparent. Mouse-on-element only:
      // rename → subtle grey, X → red.
      var BTN = 24;
      var del = row.querySelector('.branch-del');
      if (del) {
        del.style.position = 'absolute';
        del.style.right = '8px';
        del.style.top = '50%';
        del.style.transform = 'translateY(-50%)';
        del.style.width = BTN + 'px';
        del.style.height = BTN + 'px';
        del.style.display = 'none';
        del.style.alignItems = 'center';
        del.style.justifyContent = 'center';
        del.style.borderRadius = '4px';
        del.style.color = 'var(--text-muted)';
        del.style.background = 'transparent';
        del.style.cursor = 'pointer';
        del.addEventListener('mouseenter', function () {
          del.style.background = 'var(--accent-red)';
          del.style.color = '#fff';
        });
        del.addEventListener('mouseleave', function () {
          del.style.background = 'transparent';
          del.style.color = 'var(--text-muted)';
        });
      }
      var rename = row.querySelector('.branch-rename');
      if (rename) {
        rename.style.position = 'absolute';
        // X width 24 + X right offset 8 + 4px gap = 36px right offset.
        rename.style.right = '36px';
        rename.style.top = '50%';
        rename.style.transform = 'translateY(-50%)';
        rename.style.width = BTN + 'px';
        rename.style.height = BTN + 'px';
        rename.style.display = 'none';
        rename.style.alignItems = 'center';
        rename.style.justifyContent = 'center';
        rename.style.borderRadius = '4px';
        rename.style.color = 'var(--text-muted)';
        rename.style.background = 'transparent';
        rename.style.cursor = 'pointer';
        rename.addEventListener('mouseenter', function () {
          rename.style.background = 'rgba(255, 255, 255, 0.16)';
          rename.style.color = 'var(--text-bright, #fff)';
        });
        rename.addEventListener('mouseleave', function () {
          rename.style.background = 'transparent';
          rename.style.color = 'var(--text-muted)';
        });
      }
      row.addEventListener('mouseenter', function () {
        if (head) head.style.visibility = 'hidden';
        if (del) del.style.display = 'flex';
        if (rename) rename.style.display = 'inline-flex';
      });
      row.addEventListener('mouseleave', function () {
        if (head) head.style.visibility = 'visible';
        if (del) del.style.display = 'none';
        if (rename) rename.style.display = 'none';
      });
    });

    dd.addEventListener('click', function(e) {
      // Rename pencil — replace the name span with an inline input.
      // Enter submits, Esc cancels, blur submits. Empty submit triggers
      // AI auto-name; non-empty calls rename_branch.
      var ren = e.target.closest('[data-rename]');
      if (ren) {
        e.stopPropagation();
        var rhead = ren.getAttribute('data-rename');
        var current = ren.getAttribute('data-current') || '';
        var rowEl = ren.closest('.model-dd-item');
        if (!rowEl) return;
        var nameEl = rowEl.querySelector('.model-dd-name');
        if (!nameEl || nameEl.dataset.editing === '1') return;
        nameEl.dataset.editing = '1';
        var originalText = nameEl.textContent;
        // Build the input matching the surrounding row's typography.
        var inp = document.createElement('input');
        inp.type = 'text';
        inp.value = current;
        inp.placeholder = 'new branch name (empty = cancel)';
        inp.style.width = '100%';
        inp.style.boxSizing = 'border-box';
        inp.style.font = 'inherit';
        inp.style.color = 'var(--text-bright)';
        inp.style.background = 'var(--bg-input, rgba(255,255,255,0.06))';
        inp.style.border = '1px solid var(--accent-blue, #6cb4ff)';
        inp.style.borderRadius = '4px';
        inp.style.padding = '2px 6px';
        inp.style.outline = 'none';
        nameEl.textContent = '';
        nameEl.appendChild(inp);
        setTimeout(function () { inp.focus(); inp.select(); }, 0);

        var submitted = false;
        function commit(value) {
          if (submitted) return;
          submitted = true;
          var trimmed = (value || '').trim();
          // Empty submit = cancel. Previously this fired
          // `auto_name_branch`, which left the row stuck waiting on
          // an AI round-trip; users expect "I changed my mind" not
          // "now rename it for me". Esc / blur with empty value
          // also lands here.
          if (trimmed && trimmed !== current
              && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
              action: 'rename_branch',
              session_id: currentSessionId,
              head_msg_id: rhead,
              name: trimmed,
            }));
          }
          delete nameEl.dataset.editing;
          // Restore the row's text — list_branches reply will overwrite.
          nameEl.textContent = originalText;
        }
        function cancel() {
          if (submitted) return;
          submitted = true;
          delete nameEl.dataset.editing;
          nameEl.textContent = originalText;
        }
        inp.addEventListener('keydown', function (ev) {
          if (ev.key === 'Enter') { ev.preventDefault(); commit(inp.value); }
          else if (ev.key === 'Escape') { ev.preventDefault(); cancel(); }
        });
        inp.addEventListener('blur', function () { commit(inp.value); });
        // Stop click bubbling — clicks inside the input shouldn't
        // trigger checkout on the row.
        inp.addEventListener('click', function (ev) { ev.stopPropagation(); });
        return;
      }
      // Delete X — handled separately so the row's checkout doesn't fire.
      var del = e.target.closest('[data-del]');
      if (del) {
        e.stopPropagation();
        var dhead = del.getAttribute('data-del');
        if (!confirm('Delete this branch and its messages? This cannot be undone.')) return;
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            action: 'delete_branch',
            session_id: currentSessionId,
            head_msg_id: dhead,
          }));
          ws.send(JSON.stringify({
            action: 'load_session',
            session_id: currentSessionId,
          }));
        }
        _closeBranchDropdown();
        return;
      }
      var item = e.target.closest('[data-head]');
      if (!item) return;
      e.stopPropagation();
      var head = item.getAttribute('data-head');
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          action: 'checkout_branch',
          session_id: currentSessionId,
          head_msg_id: head,
        }));
        ws.send(JSON.stringify({
          action: 'load_session',
          session_id: currentSessionId,
        }));
      }
      _closeBranchDropdown();
    });

    setTimeout(function() {
      document.addEventListener('click', _branchDropdownDocClick, { once: true });
    }, 0);
  });
}
window.openBranchDropdown = openBranchDropdown;

function _branchDropdownDocClick(e) {
  var dd = document.getElementById('branchDropdown');
  if (dd && dd.contains(e.target)) {
    document.addEventListener('click', _branchDropdownDocClick, { once: true });
    return;
  }
  _closeBranchDropdown();
}

function _closeBranchDropdown() {
  var dd = document.getElementById('branchDropdown');
  if (dd) dd.remove();
  document.removeEventListener('click', _branchDropdownDocClick);
}

function switchSession(sessionId) {
  // If already on this conversation, just reload in-place
  if (sessionId === currentSessionId && window.location.pathname === '/s/' + sessionId) {
    return;
  }
  if (window.__navigate) { window.__navigate('/s/' + sessionId); return; }
  window.location.href = '/s/' + sessionId;
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

function deleteSession(sessionId) {
  var conv = conversations[sessionId];
  var title = (conv && conv.title) || 'Untitled';
  _showConfirm('Delete chat', 'Are you sure you want to delete "' + title + '"?', function() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'delete_session', session_id: sessionId }));
    }
    delete conversations[sessionId];
    if (currentSessionId === sessionId) {
      newSession();
    }
    renderSessions();
  });
}

function clearAllSessions() {
  var count = Object.keys(conversations).length;
  if (!count) return;
  _showConfirm('Delete all chats', 'Are you sure you want to delete all ' + count + ' conversations? This cannot be undone.', function() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'clear_sessions' }));
    }
    conversations = {};
    newSession();
    renderSessions();
  });
}

function newSession() {
  if (window.location.pathname !== '/chat') {
    if (window.__navigate) { window.__navigate('/chat'); return; }
    window.location.href = '/chat';
    return;
  }
  // Already on /, reset in-place
  currentSessionId = null;
  history.replaceState(null, '', '/chat');
  pendingResponses = {};
  trees = [];
  var container = document.getElementById('chatMessages');
  container.innerHTML = '';
  var welcome = document.createElement('div');
  welcome.className = 'welcome';
  welcome.id = 'welcomeScreen';
  welcome.innerHTML =
    '<div class="welcome-top">' +
      '<div class="welcome-logo">{<span class="logo-l1">L</span><span class="logo-l2">L</span><span class="logo-m">M</span><span class="welcome-logo-caret"></span>}</div>' +
      '<div class="welcome-title">Agentic Programming</div>' +
      '<div class="welcome-text">Run agentic functions, create new ones, or ask questions. Type a command or natural language below.</div>' +
    '</div>';
  window._pendingChannelChoice = null;
  if (typeof window.refreshChannelBadge === 'function') window.refreshChannelBadge();
  container.appendChild(welcome);
  setWelcomeVisible(true);
  renderSessions();
  // Clear the right-sidebar Branches panel — without this the previous
  // session's branch chip lingers on the welcome screen.
  if (typeof window.renderBranchesPanel === 'function') {
    try { window.renderBranchesPanel(); } catch (e) {}
  }
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
  // Reset session-scoped chips that aren't covered by loadAgentSettings:
  // status badge (was showing previous session's "WeChat (xxx) · ...")
  // and branch chip (was showing previous session's branch list).
  if (typeof window.refreshStatusSource === 'function') window.refreshStatusSource();
  if (typeof window.refreshBranchBadge === 'function') {
    // Wipe local cache for the branch chip so it doesn't flash the old
    // session's branches before realising there's no current session.
    if (typeof _branchesByConv !== 'undefined') {
      // _branchesByConv is module-local in conversations.js — drop all keys.
      Object.keys(_branchesByConv).forEach(function (k) { delete _branchesByConv[k]; });
    }
    window.refreshBranchBadge();
  }
}

function loadSessionData(data) {
  if (!data.messages) data.messages = [];
  // Merge instead of replace so fields populated by an earlier
  // sessions_list (e.g. channel / account_id, which session_loaded
  // didn't always carry) survive when the load response lands.
  conversations[data.id] = Object.assign({}, conversations[data.id] || {}, data);
  renderSessions();
  // Reset branches panel to collapsed on every new conversation load.
  window._branchesPanelCollapsed = true;
  if (data.id === currentSessionId) {
    if (typeof window.refreshStatusSource === 'function') window.refreshStatusSource();
    if (typeof window.refreshChannelBadge === 'function') window.refreshChannelBadge();
    // Pull the latest branch list so the chip reflects this conv's
    // current head + alternates. fetchBranches caches per conv; we
    // invalidate to force a fresh server snapshot.
    delete _branchesByConv[data.id];
    fetchBranches(data.id).then(function() {
      if (typeof window.refreshBranchBadge === 'function') window.refreshBranchBadge();
    });
  }
  if (data.id === currentSessionId) {
    var area = document.getElementById('chatArea');
    var hasSavedScroll = !!sessionStorage.getItem('agentic_scroll');
    if (hasSavedScroll) _skipScrollToBottom = true;
    renderSessionMessages(data);
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

function renderSessionMessages(conv) {
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
        var restoredEl = _buildRestoredRuntimeBlock(msg, nextMsg, mi);
        // Stamp BOTH underlying message ids so the history-graph
        // visibility sync can light up both runtime-display nodes
        // (user-call + assistant-result) when this merged block is
        // on screen. data-msg-id stays single for the action bar's
        // retry/branch targeting — nextMsg.id is the assistant id
        // which the retry endpoint walks back from correctly.
        if (nextMsg.id) restoredEl.setAttribute('data-msg-id', nextMsg.id);
        var ids = [];
        if (msg.id) ids.push(msg.id);
        if (nextMsg.id) ids.push(nextMsg.id);
        if (ids.length) restoredEl.setAttribute('data-msg-ids', ids.join(' '));
        container.appendChild(restoredEl);
        mi++;
        continue;
      }
      var interruptedEl = _buildInterruptedRuntimeBlock(msg);
      if (msg.id) interruptedEl.setAttribute('data-msg-id', msg.id);
      container.appendChild(interruptedEl);
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
    // Stamp msg_id so the hover action bar (copy/retry/branch) can
    // target the right server-side message. The retry endpoint
    // walks back to the nearest user turn, so passing an assistant
    // id (may be "{x}_reply") is equally valid.
    if (msg.id) div.setAttribute('data-msg-id', msg.id);
    // ContextGit metadata — sibling counts drive <N/M> nav, timestamp
    // drives the hover tooltip. See message-actions-nav.js.
    if (msg.sibling_index && msg.sibling_total) {
      div.setAttribute('data-sibling-index', String(msg.sibling_index));
      div.setAttribute('data-sibling-total', String(msg.sibling_total));
      // Server provides direct prev/next ids because the client only
      // holds the linear chain under HEAD — sibling branches aren't
      // in _allMessages. See server load_session handler.
      if (msg.prev_sibling_id) div.setAttribute('data-prev-sibling', msg.prev_sibling_id);
      if (msg.next_sibling_id) div.setAttribute('data-next-sibling', msg.next_sibling_id);
    }
    if (msg.timestamp || msg.created_at) {
      var ts = msg.timestamp || msg.created_at;
      // Stamp the raw timestamp as a data attr only. The visible
      // hover target is a small badge rendered by message-actions.js
      // inside the action bar — NOT `div.title`, which would make
      // the native browser tooltip fire on the entire bubble.
      var tsMs = ts > 1e12 ? ts : ts * 1000;
      div.setAttribute('data-created-at', String(tsMs));
    }
    container.appendChild(div);
    if (typeof window.ensureMessageActions === 'function') {
      window.ensureMessageActions(div);
    }
  }

  // Expose the full message list to the nav module so it can walk
  // siblings without a round-trip. Populated here since this is the
  // only place we see the whole conversation at once.
  window._allMessages = conv.messages.slice();
  // Refresh the History DAG panel if it's wired up. The graph is the
  // full conversation (every branch), not just the HEAD chain, so
  // it comes from a separate field on the server payload.
  if (typeof window.renderHistoryGraph === 'function') {
    window.renderHistoryGraph(conv.graph || [], conv.head_id || null);
  }
  // Container-level run_active flag — CSS greys out Edit/Retry when
  // true. Flipped elsewhere when runs start / end; set it from the
  // snapshot we just loaded so initial state is right.
  var chatContainer = document.getElementById('chatMessages');
  if (chatContainer) {
    chatContainer.setAttribute(
      'data-run-active', conv.run_active ? 'true' : 'false',
    );
  }

  // Re-attach any in-flight assistant placeholders that this
  // re-render detached. renderSessionMessages clears the chat
  // container above; _renderChatStreamEvent still mutates the
  // pendingResponses node (now detached) so tool_use / tool_result
  // bubbles accumulate in memory but the user sees nothing until
  // _handleChatResult re-attaches at the end. Re-attaching here means
  // retry's tool calls render live, not all-at-once on completion.
  // Re-attach in-flight placeholders that this re-render detached.
  // Two guards prevent ghost bubbles on branch switches:
  //   * skip when no run is active — a placeholder lingering past
  //     run completion is by definition orphan, drop it
  //   * skip when the placeholder's key isn't on the current branch
  //     (e.g. a run on a sibling that the user just navigated away
  //     from); the run still owns it but it shouldn't render here
  try {
    var _runActive = (typeof window.isRunning !== 'undefined' && window.isRunning)
                  || (typeof isRunning !== 'undefined' && isRunning);
    var idsOnBranch = {};
    (conv.messages || []).forEach(function (m) {
      if (m && m.id) idsOnBranch[m.id] = true;
    });
    Object.keys(pendingResponses || {}).forEach(function (k) {
      var ph = pendingResponses[k];
      if (!ph || document.body.contains(ph)) return;
      // Key is on this branch (the user msg the assistant is replying
      // to) → re-attach. Brand-new retry/edit puts its placeholder
      // key as the just-forked user msg, which is the new HEAD and
      // therefore on the branch we're about to render. So this also
      // covers the live-streaming retry case.
      if (_runActive && idsOnBranch[k]) {
        container.appendChild(ph);
      } else if (!_runActive) {
        delete pendingResponses[k];
      }
      // else: run is active but key not on this branch — leave the
      // node detached (still in pendingResponses for the owner branch
      // to find on its next render).
    });
  } catch (e) {}

  // Branch switch / checkout pivot: scroll to the message the user
  // clicked instead of the bottom of the new branch. Set by
  // history-graph.js / message-actions-nav.js before they fire
  // load_session.
  var pivot = window._postCheckoutScrollTo;
  if (pivot) {
    window._postCheckoutScrollTo = null;
    // Scope strictly to chatMessages — history-graph nodes in the
    // right sidebar ALSO carry data-msg-id, so a plain selector picks
    // the SVG node first and scrollIntoView jumps the wrong panel.
    var pivotEl = null;
    var key = window.CSS && CSS.escape ? CSS.escape(pivot) : pivot;
    var matches = container.querySelectorAll('[data-msg-id="' + key + '"], [data-msg-ids~="' + key + '"]');
    if (matches.length) pivotEl = matches[0];
    if (pivotEl) {
      requestAnimationFrame(function () {
        pivotEl.scrollIntoView({ behavior: 'auto', block: 'start' });
      });
      _skipScrollToBottom = false;
      return;
    }
  }

  if (!_skipScrollToBottom) scrollToBottom({ force: true });
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
  // Plain chat with persisted thinking/tool blocks — rebuild the same
  // collapsible scaffold the live stream produced.
  // Backfill: messages stored before the dispatcher started writing
  // `blocks` only carry `tool_calls` (slim {tool, result, is_error}).
  // Synthesize a minimal blocks array from that so the user still
  // sees a tool history after refresh instead of a wall of bare text.
  var _blocks = msg.blocks;
  if ((!_blocks || !_blocks.length) && !msg.function && msg.tool_calls && msg.tool_calls.length) {
    _blocks = msg.tool_calls.map(function (tc) {
      return {
        type: 'tool',
        tool: tc.tool,
        tool_call_id: tc.id || tc.tool_call_id || null,
        input: tc.input || '',
        result: tc.result,
        is_error: tc.is_error,
      };
    });
  }
  var hasBlocks = !msg.function && _blocks && _blocks.length;
  if (hasBlocks && typeof _renderAssistantBlocks === 'function') {
    cHtml = _renderAssistantBlocks(_blocks, msg.content || '');
    div.innerHTML =
      '<div class="message-header">' +
        '<div class="message-avatar bot-avatar">A</div>' +
        '<div class="message-sender">Agentic</div>' +
      '</div>' + cHtml;
    return div;
  }
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

  if (currentSessionId && conversations[currentSessionId]) {
    var conv = conversations[currentSessionId];
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
    renderSessionMessages(conv);
    var el = document.querySelector('[data-function="' + data.function + '"]');
    if (el) {
      requestAnimationFrame(function() { el.scrollIntoView({ block: 'center' }); });
    }
  }
}

// ===== Functions Panel =====

