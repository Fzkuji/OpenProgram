// ===== Agent switcher + per-agent sessions =====
//
// Multi-agent data model (see openprogram/agents/manager.py):
//   every conversation belongs to exactly one agent;
//   the sidebar shows conversations for the "current" agent only;
//   switching agents re-filters the sidebar without touching any state.
//
// State this file owns:
//   currentAgentId — which agent we're viewing (default agent on boot)
//   agents[]       — registry list fetched from the server
//
// The sidebar render (sidebar.js) reads currentAgentId to decide which
// conversations to show. We re-call renderSessions() on every
// agent-list refresh and every switcher click.

var currentAgentId = null;
var agents = [];

function agentById(id) {
  for (var i = 0; i < agents.length; i++) {
    if (agents[i].id === id) return agents[i];
  }
  return null;
}

function _handleAgentsList(data) {
  agents = (data || []).slice().sort(function(a, b) {
    return (a.created_at || 0) - (b.created_at || 0);
  });
  if (!currentAgentId) {
    var def = agents.find(function(a) { return a.default; }) || agents[0];
    currentAgentId = def ? def.id : null;
  } else if (!agentById(currentAgentId) && agents.length) {
    currentAgentId = agents[0].id;
  }
  renderAgentSwitcher();
  if (typeof renderSessions === 'function') {
    renderSessions();
  }
}

function renderAgentSwitcher() {
  var el = document.getElementById('agentSwitcher');
  if (!el) return;
  var a = agentById(currentAgentId);
  if (!a) {
    el.textContent = 'No agents';
    return;
  }
  el.textContent = 'Agent: ' + (a.name || a.id);
  el.classList.toggle('is-default', !!a.default);
}

function switchAgent(id) {
  if (!id || id === currentAgentId) return;
  currentAgentId = id;
  renderAgentSwitcher();
  if (typeof renderSessions === 'function') renderSessions();
  // If we were on a conv belonging to a different agent, drop back
  // to /new so the user isn't staring at a conv that's hidden from
  // the current agent's list.
  if (currentSessionId) {
    var conv = conversations[currentSessionId];
    if (conv && conv.agent_id && conv.agent_id !== currentAgentId) {
      if (typeof newSession === 'function') {
        newSession();
      }
    }
  }
}

function openAgentSwitcher() {
  if (!agents.length) {
    alert('No agents configured. Run `openprogram agents add main`.');
    return;
  }
  var overlay = document.createElement('div');
  overlay.className = 'confirm-overlay visible';

  var rowsHtml = '';
  for (var i = 0; i < agents.length; i++) {
    var a = agents[i];
    var tag = a.default ? ' <span class="agent-row-tag">default</span>' : '';
    var active = a.id === currentAgentId ? ' active' : '';
    var pm = (a.model && a.model.provider && a.model.id)
      ? a.model.provider + '/' + a.model.id
      : 'no model';
    rowsHtml +=
      '<div class="agent-row-wrap' + active + '">' +
        '<button class="agent-row" data-aid="' + escAttr(a.id) + '" title="Switch to this agent">' +
          '<div class="agent-row-title">' + escHtml(a.name || a.id) + tag + '</div>' +
          '<div class="agent-row-sub">' + escHtml(pm) + ' · ' +
            escHtml(a.thinking_effort || 'medium') + ' effort</div>' +
        '</button>' +
        '<button class="agent-row-bindings" data-aid="' + escAttr(a.id) +
            '" title="Channel connections for this agent">⇄</button>' +
      '</div>';
  }

  overlay.innerHTML =
    '<div class="confirm-dialog">' +
      '<div class="confirm-title">Agents</div>' +
      '<div class="confirm-message" style="text-align:left;font-size:12px;color:var(--text-muted);margin:0 0 4px">' +
        'Click an agent to switch. ⇄ opens channel connections.' +
      '</div>' +
      '<div class="agent-list">' + rowsHtml + '</div>' +
      '<div class="confirm-actions">' +
        '<button class="confirm-btn" id="_agCancel">Close</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(overlay);

  function close() {
    overlay.classList.remove('visible');
    overlay.addEventListener('transitionend', function() { overlay.remove(); });
  }
  overlay.querySelector('#_agCancel').onclick = close;
  overlay.addEventListener('click', function(e) {
    if (e.target === overlay) close();
  });
  overlay.querySelectorAll('.agent-row').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var id = btn.getAttribute('data-aid');
      close();
      switchAgent(id);
    });
  });
  overlay.querySelectorAll('.agent-row-bindings').forEach(function(btn) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();
      var id = btn.getAttribute('data-aid');
      close();
      openAgentBindingsDialog(id);
    });
  });
}

function openSessionAttachDialog() {
  if (!currentSessionId) {
    alert('Open a conversation first, then Connect channel lets you ' +
          'route a WeChat/Telegram/etc. user into it.');
    return;
  }
  if (!ws || ws.readyState !== WebSocket.OPEN) return;

  // Fetch accounts + current aliases so we can render.
  var state = { accounts: null, aliases: null };
  var origHandler = window._agentsBindingsTemp;
  window._agentsBindingsTemp = function(msg) {
    if (msg.type === 'channel_accounts') {
      state.accounts = msg.data || [];
      tryRender();
    } else if (msg.type === 'session_aliases') {
      state.aliases = msg.data || [];
      tryRender();
    }
  };
  ws.send(JSON.stringify({ action: 'list_channel_accounts' }));
  ws.send(JSON.stringify({ action: 'list_session_aliases' }));

  var overlay = document.createElement('div');
  overlay.className = 'confirm-overlay visible';
  overlay.innerHTML = '<div class="confirm-dialog"><div class="confirm-title">Loading...</div></div>';
  document.body.appendChild(overlay);

  function close() {
    window._agentsBindingsTemp = origHandler;
    overlay.classList.remove('visible');
    overlay.addEventListener('transitionend', function() { overlay.remove(); });
  }

  function tryRender() {
    if (state.accounts === null || state.aliases === null) return;
    var mine = state.aliases.filter(function(a) {
      return a.session_id === currentSessionId;
    });
    var existingHtml = '';
    if (mine.length) {
      existingHtml = '<div class="bind-section">';
      for (var i = 0; i < mine.length; i++) {
        var a = mine[i];
        var summary = a.channel + ':' + a.account_id + '  ' +
          a.peer.kind + ':' + a.peer.id;
        existingHtml += '<div class="bind-row">' +
          '<span class="bind-row-label">' + escHtml(summary) + '</span>' +
          '<button class="bind-row-rm" data-ch="' + escAttr(a.channel) +
            '" data-ac="' + escAttr(a.account_id) +
            '" data-pk="' + escAttr(a.peer.kind) +
            '" data-pi="' + escAttr(a.peer.id) + '">×</button>' +
        '</div>';
      }
      existingHtml += '</div>';
    }

    var acctOptionsHtml = '';
    for (var j = 0; j < state.accounts.length; j++) {
      var acc = state.accounts[j];
      acctOptionsHtml += '<option value="' +
        escAttr(acc.channel + '|' + acc.account_id) + '">' +
        escHtml(acc.channel + ' · ' + acc.account_id) + '</option>';
    }
    if (!acctOptionsHtml) {
      acctOptionsHtml = '<option value="">(no channel accounts — ' +
        'run `openprogram channels accounts add` first)</option>';
    }

    overlay.querySelector('.confirm-dialog').innerHTML =
      '<div class="confirm-title">Connect channel to this session</div>' +
      '<div class="confirm-message" style="text-align:left;font-size:12px;color:var(--text-muted);margin:0 0 4px">' +
        'Route a channel user\'s messages into the current session ' +
        '(session_id: ' + escHtml(currentSessionId) + ').' +
      '</div>' +
      existingHtml +
      '<div class="bind-add">' +
        '<div class="bind-field"><label class="bind-label">Channel account</label>' +
          '<select id="_saAcct" class="bind-input">' + acctOptionsHtml + '</select></div>' +
        '<div class="bind-field"><label class="bind-label">Peer kind</label>' +
          '<select id="_saKind" class="bind-input">' +
            '<option value="direct">direct (DM)</option>' +
            '<option value="group">group</option>' +
            '<option value="channel">channel</option>' +
          '</select></div>' +
        '<div class="bind-field"><label class="bind-label">Peer id</label>' +
          '<input id="_saPeer" class="bind-input" placeholder="WeChat openid / Telegram chat_id / ...">' +
        '</div>' +
        '<button class="confirm-btn" id="_saAdd">Attach</button>' +
      '</div>' +
      '<div class="confirm-actions">' +
        '<button class="confirm-btn" id="_saClose">Close</button>' +
      '</div>';

    overlay.querySelector('#_saClose').onclick = close;
    overlay.querySelectorAll('.bind-row-rm').forEach(function(btn) {
      btn.onclick = function() {
        ws.send(JSON.stringify({
          action: 'detach_session',
          channel: btn.getAttribute('data-ch'),
          account_id: btn.getAttribute('data-ac'),
          peer_kind: btn.getAttribute('data-pk'),
          peer_id: btn.getAttribute('data-pi'),
        }));
        state.aliases = state.aliases.filter(function(a) {
          return !(a.channel === btn.getAttribute('data-ch') &&
                   a.account_id === btn.getAttribute('data-ac') &&
                   a.peer.kind === btn.getAttribute('data-pk') &&
                   a.peer.id === btn.getAttribute('data-pi'));
        });
        tryRender();
      };
    });
    overlay.querySelector('#_saAdd').onclick = function() {
      var raw = overlay.querySelector('#_saAcct').value;
      if (!raw) { alert('No channel account — add one first.'); return; }
      var parts = raw.split('|');
      var peerId = overlay.querySelector('#_saPeer').value.trim();
      if (!peerId) { alert('Peer id is required.'); return; }
      var peerKind = overlay.querySelector('#_saKind').value;
      ws.send(JSON.stringify({
        action: 'attach_session',
        session_id: currentSessionId,
        channel: parts[0],
        account_id: parts[1],
        peer_kind: peerKind,
        peer_id: peerId,
      }));
      setTimeout(function() {
        ws.send(JSON.stringify({ action: 'list_session_aliases' }));
      }, 200);
    };
  }

  overlay.addEventListener('click', function(e) {
    if (e.target === overlay) close();
  });
}

function openAgentBindingsDialog(agentId) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  // Request fresh data — we re-render after both land.
  var state = { bindings: null, accounts: null };

  function tryRender() {
    if (state.bindings === null || state.accounts === null) return;
    render();
  }

  var origHandler = window._agentsBindingsTemp;
  window._agentsBindingsTemp = function(msg) {
    if (msg.type === 'channel_bindings') {
      state.bindings = msg.data || [];
      tryRender();
    } else if (msg.type === 'channel_accounts') {
      state.accounts = msg.data || [];
      tryRender();
    }
  };

  ws.send(JSON.stringify({ action: 'list_channel_bindings' }));
  ws.send(JSON.stringify({ action: 'list_channel_accounts' }));

  var overlay = document.createElement('div');
  overlay.className = 'confirm-overlay visible';
  overlay.innerHTML =
    '<div class="confirm-dialog">' +
      '<div class="confirm-title">Loading ' + escHtml(agentId) + '...</div>' +
    '</div>';
  document.body.appendChild(overlay);

  function close() {
    window._agentsBindingsTemp = origHandler;
    overlay.classList.remove('visible');
    overlay.addEventListener('transitionend', function() { overlay.remove(); });
  }

  function render() {
    var mine = state.bindings.filter(function(b) { return b.agent_id === agentId; });
    var agent = agentById(agentId) || { id: agentId, name: agentId };
    var bindingRowsHtml = '';
    if (!mine.length) {
      bindingRowsHtml = '<div class="bind-empty">No channels connected yet. Add one below.</div>';
    } else {
      for (var i = 0; i < mine.length; i++) {
        var b = mine[i];
        var m = b.match || {};
        var peer = m.peer || null;
        var summary = (m.channel || '*') + ' · account=' + (m.account_id || '*');
        if (peer) summary += ' · peer=' + (peer.kind || '?') + ':' + (peer.id || '?');
        bindingRowsHtml +=
          '<div class="bind-row">' +
            '<span class="bind-row-label">' + escHtml(summary) + '</span>' +
            '<button class="bind-row-rm" data-bid="' + escAttr(b.id) + '" title="Remove">×</button>' +
          '</div>';
      }
    }

    var acctOptionsByChannel = {};
    for (var j = 0; j < state.accounts.length; j++) {
      var acc = state.accounts[j];
      (acctOptionsByChannel[acc.channel] = acctOptionsByChannel[acc.channel] || []).push(acc);
    }
    var acctOptionsHtml = '';
    var allChannels = ['wechat', 'telegram', 'discord', 'slack'];
    for (var ci = 0; ci < allChannels.length; ci++) {
      var ch = allChannels[ci];
      var accs = acctOptionsByChannel[ch] || [];
      for (var ai = 0; ai < accs.length; ai++) {
        acctOptionsHtml += '<option value="' + escAttr(ch + '|' + accs[ai].account_id) + '">' +
          escHtml(ch + ' · ' + accs[ai].account_id) + '</option>';
      }
    }
    if (!acctOptionsHtml) {
      acctOptionsHtml = '<option value="">(no channel accounts — run `openprogram channels accounts add`)</option>';
    }

    overlay.querySelector('.confirm-dialog').innerHTML =
      '<div class="confirm-title">' + escHtml(agent.name || agent.id) + ' · Channels</div>' +
      '<div class="confirm-message" style="text-align:left;font-size:12px;color:var(--text-muted);margin:0 0 8px">' +
        'Inbound messages matching a rule below route to this agent.' +
      '</div>' +
      '<div class="bind-section">' + bindingRowsHtml + '</div>' +
      '<div class="bind-add">' +
        '<div class="bind-field"><label class="bind-label">Add connection</label>' +
          '<select id="_bindAcct" class="bind-input">' + acctOptionsHtml + '</select></div>' +
        '<div class="bind-field"><label class="bind-label">Specific peer id (optional, blank = whole account)</label>' +
          '<input id="_bindPeer" class="bind-input" placeholder="e.g. WeChat openid, Telegram chat_id"></div>' +
        '<button class="confirm-btn" id="_bindAdd">Attach</button>' +
      '</div>' +
      '<div class="confirm-actions">' +
        '<button class="confirm-btn" id="_bindClose">Close</button>' +
      '</div>';

    overlay.querySelector('#_bindClose').onclick = close;
    overlay.querySelectorAll('.bind-row-rm').forEach(function(btn) {
      btn.onclick = function() {
        ws.send(JSON.stringify({
          action: 'remove_binding', binding_id: btn.getAttribute('data-bid'),
        }));
        state.bindings = state.bindings.filter(function(b) {
          return b.id !== btn.getAttribute('data-bid');
        });
        render();
      };
    });
    overlay.querySelector('#_bindAdd').onclick = function() {
      var raw = overlay.querySelector('#_bindAcct').value;
      if (!raw) return;
      var parts = raw.split('|');
      var peerId = overlay.querySelector('#_bindPeer').value.trim();
      var body = {
        action: 'add_binding',
        agent_id: agentId,
        channel: parts[0],
        account_id: parts[1],
      };
      if (peerId) body.peer = { kind: 'direct', id: peerId };
      ws.send(JSON.stringify(body));
      // Optimistic refresh — the server will also broadcast
      // binding_changed so state stays current.
      setTimeout(function() {
        ws.send(JSON.stringify({ action: 'list_channel_bindings' }));
      }, 200);
    };
  }

  overlay.addEventListener('click', function(e) {
    if (e.target === overlay) close();
  });
}

function _handleAgentChanged(data) {
  // Server broadcast on add / delete / default-change: refetch.
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action: 'list_agents' }));
  }
}
