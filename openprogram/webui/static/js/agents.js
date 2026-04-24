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
// conversations to show. We re-call renderConversations() on every
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
  if (typeof renderConversations === 'function') {
    renderConversations();
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
  if (typeof renderConversations === 'function') renderConversations();
  // If we were on a conv belonging to a different agent, drop back
  // to /new so the user isn't staring at a conv that's hidden from
  // the current agent's list.
  if (currentConvId) {
    var conv = conversations[currentConvId];
    if (conv && conv.agent_id && conv.agent_id !== currentAgentId) {
      if (typeof newConversation === 'function') {
        newConversation();
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
      '<button class="agent-row' + active + '" data-aid="' + escAttr(a.id) + '">' +
        '<div class="agent-row-title">' + escHtml(a.name || a.id) + tag + '</div>' +
        '<div class="agent-row-sub">' + escHtml(pm) + ' · ' +
          escHtml(a.thinking_effort || 'medium') + ' effort</div>' +
      '</button>';
  }

  overlay.innerHTML =
    '<div class="confirm-dialog">' +
      '<div class="confirm-title">Switch agent</div>' +
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
}

function _handleAgentChanged(data) {
  // Server broadcast on add / delete / default-change: refetch.
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action: 'list_agents' }));
  }
}
