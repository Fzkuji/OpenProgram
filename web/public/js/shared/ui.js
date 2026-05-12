// ===== UI State: Running, Pause, Detail Panel, Thinking, Code Viewer =====

function setRunning(running) {
  isRunning = running;
  if (!running) isPaused = false;
  updateSendBtn();
  var chatInput = document.getElementById('chatInput');
  if (chatInput) {
    chatInput.placeholder = running ? 'Waiting for response...' : 'create / run / fix or ask anything...';
  }
  var fnRunBtns = document.querySelectorAll('.fn-form-run-btn');
  for (var i = 0; i < fnRunBtns.length; i++) {
    fnRunBtns[i].disabled = running;
    fnRunBtns[i].style.opacity = running ? '0.4' : '';
    fnRunBtns[i].style.cursor = running ? 'not-allowed' : '';
  }
}

function updateContextStats(messages) {
  // No-op: real stats come from the server via _handleContextStats.
}

var _svgSend = '<svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>';
var _svgPause = '<svg viewBox="0 0 24 24"><rect x="5" y="4" width="4" height="16" rx="1"/><rect x="15" y="4" width="4" height="16" rx="1"/></svg>';
var _svgResume = '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';

function updateSendBtn() {
  var sendBtn = document.getElementById('sendBtn');
  var stopBtn = document.getElementById('stopBtn');
  var badge = document.getElementById('statusBadge');

  if (!isRunning) {
    sendBtn.innerHTML = _svgSend;
    sendBtn.title = 'Send message';
    sendBtn.className = 'send-btn';
    stopBtn.style.display = 'none';
  } else if (isPaused) {
    sendBtn.innerHTML = _svgResume;
    sendBtn.title = 'Resume';
    sendBtn.className = 'send-btn paused-state';
    stopBtn.style.display = 'flex';
    badge.textContent = 'paused';
    badge.className = 'status-badge paused';
  } else {
    sendBtn.innerHTML = _svgPause;
    sendBtn.title = 'Pause';
    sendBtn.className = 'send-btn';
    stopBtn.style.display = 'none';
    badge.textContent = 'running';
    badge.className = 'status-badge';
  }
}

function updatePauseBtn() { updateSendBtn(); }

function updateStatus(status, source) {
  // Two-part badge: a `.badge-short` always-visible label
  // ("connected" / "disconnected") + a `.badge-details` span that the
  // CSS container query hides when the topbar is narrow. The details
  // are also stuffed into `title` so a hover still surfaces them when
  // collapsed. This keeps the layout from overflowing on narrow
  // viewports without losing information.
  var badge = document.getElementById('statusBadge');
  if (!badge) return;
  var connected = status === 'connected';
  var shortLabel = connected ? 'connected' : 'disconnected';
  var details = connected && source ? ' · ' + source : '';
  badge.innerHTML = ''
    + '<span class="badge-short">' + shortLabel + '</span>'
    + (details
        ? '<span class="badge-details">' + escapeHtml(details) + '</span>'
        : '');
  badge.title = connected && source ? 'session source: ' + source : '';
  badge.className = connected ? 'status-badge' : 'status-badge disconnected';
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Strip backend-generated placeholder titles like "WeChat: o9cq..."
// (the dispatcher uses these as a fallback when a channel session has
// no real title yet). We don't want them shown — they leak the raw
// account id and crowd out anything useful.
function _isPlaceholderTitle(title) {
  if (!title) return true;
  if (title === 'New conversation' || title === 'Untitled') return true;
  // "WeChat: o9cq...", "Discord: 1234567...", etc. — channel prefix +
  // a long opaque id. Treat as placeholder.
  return /^(wechat|discord|telegram|slack)\s*[:：]\s*\S{8,}/i.test(title);
}

var _CHANNEL_BRAND = { wechat: 'WeChat', discord: 'Discord', telegram: 'Telegram', slack: 'Slack' };
function _channelBrand(channel) {
  if (!channel) return '';
  return _CHANNEL_BRAND[String(channel).toLowerCase()] || channel;
}
function _channelPrefixFor(channel, accountId) {
  if (!channel) return '';
  var brand = _channelBrand(channel);
  return accountId ? brand + ' (' + accountId + ')' : brand;
}

function _displayTitleFor(conv) {
  if (!conv) return '';
  var t = (conv.title || '').trim();
  if (_isPlaceholderTitle(t)) return '';
  return t.length > 30 ? t.slice(0, 30) + '…' : t;
}
window._channelPrefixFor = _channelPrefixFor;
window._displayTitleFor = _displayTitleFor;
window._isPlaceholderTitle = _isPlaceholderTitle;

function refreshStatusSource() {
  var cid = (typeof currentSessionId !== 'undefined') ? currentSessionId : null;
  var conv = (cid && typeof conversations !== 'undefined') ? conversations[cid] : null;

  // Pick the channel/account triple to display: the conv's binding if
  // it has one, otherwise the user's pending choice from the welcome
  // picker. So the badge updates instantly when the user picks on a
  // brand-new chat.
  var ch = null, acct = null;
  if (conv && conv.channel) {
    ch = conv.channel;
    acct = conv.account_id || null;
  } else if (window._pendingChannelChoice && window._pendingChannelChoice.channel) {
    ch = window._pendingChannelChoice.channel;
    acct = window._pendingChannelChoice.account_id || null;
  }

  var parts = [];
  if (ch) {
    parts.push(_channelPrefixFor(ch, acct));
  } else if (conv && conv.source) {
    parts.push(conv.source);
  }
  var title = _displayTitleFor(conv);
  if (title) parts.push(title);
  updateStatus('connected', parts.join(' · '));
}
window.refreshStatusSource = refreshStatusSource;

// ===== Pause/Resume =====

function onSendBtnClick() {
  if (isRunning) {
    togglePause();
  } else {
    sendMessage();
  }
}

function togglePause() {
  var endpoint = isPaused ? '/api/resume' : '/api/pause';
  fetch(endpoint, { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      isPaused = data.paused;
      updateSendBtn();
    })
    .catch(function() {});
}

function stopExecution() {
  if (!currentSessionId) {
    isPaused = false;
    isRunning = false;
    updateSendBtn();
    return;
  }
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
      addSystemMessage('Execution stopped.');
    })
    .catch(function() {
      isPaused = false;
      isRunning = false;
      updateSendBtn();
    });
}

// ===== Thinking Effort =====

function buildThinkingMenu() {
  var cfg = _fnFormActive
    ? (_agentSettings && _agentSettings.exec && _agentSettings.exec.thinking) || _thinkingConfig
    : _thinkingConfig;
  if (!cfg) return;
  var menu = document.getElementById('thinkingMenu');
  var label = document.getElementById('thinkingLabel');
  var selector = document.getElementById('thinkingSelector');
  if (!menu || !label) return;

  // Model-driven: empty options = this model doesn't support thinking.
  // Hide the whole selector+menu instead of showing an empty dropdown.
  var options = (cfg.options || []).slice();
  if (!options.length) {
    if (selector) selector.style.display = 'none';
    menu.classList.remove('open');
    if (_fnFormActive) _execThinkingEffort = null;
    else _thinkingEffort = null;
    return;
  }
  if (selector) selector.style.display = '';

  var currentEffort = _fnFormActive ? _execThinkingEffort : _thinkingEffort;
  var values = options.map(function(o) { return o.value; });
  if (values.indexOf(currentEffort) < 0) {
    currentEffort = cfg.default || values[0];
    if (_fnFormActive) _execThinkingEffort = currentEffort;
    else _thinkingEffort = currentEffort;
  }
  label.textContent = 'effort: ' + currentEffort;

  menu.innerHTML = options.map(function(o) {
    var sel = o.value === currentEffort;
    return '<div class="thinking-option' + (sel ? ' selected' : '') + '" onclick="setThinkingEffort(\'' + o.value + '\')">' +
      '<span class="thinking-opt-label">' + o.value + '</span>' +
      '<span class="thinking-opt-desc">' + o.desc + '</span>' +
      '<span class="thinking-opt-check">' + (sel ? '&#10003;' : '') + '</span>' +
    '</div>';
  }).join('');
}

window._closeAllPopovers = function(except) {
  if (except !== 'thinking') {
    var tm = document.getElementById('thinkingMenu');
    var ts = document.getElementById('thinkingSelector');
    if (tm) tm.classList.remove('open');
    if (ts) ts.classList.remove('open');
  }
  if (except !== 'plus') {
    var pm = document.getElementById('plusMenu');
    var pb = document.getElementById('plusBtn');
    if (pm) pm.classList.remove('open');
    if (pb) pb.classList.remove('open');
  }
  if (except !== 'model') {
    var md = document.getElementById('modelDropdown');
    if (md) md.remove();
  }
  if (except !== 'user') {
    var um = document.getElementById('userMenu');
    if (um) um.classList.remove('open');
  }
  if (except !== 'agent') {
    var ag = document.getElementById('agentSelector');
    if (ag) ag.remove();
  }
  if (except !== 'channel') {
    var ch = document.getElementById('channelDropdown');
    if (ch) ch.remove();
  }
  if (except !== 'branch') {
    var br = document.getElementById('branchDropdown');
    if (br) br.remove();
  }
};

function toggleThinkingMenu(e) {
  e.stopPropagation();
  var menu = document.getElementById('thinkingMenu');
  var sel = document.getElementById('thinkingSelector');
  var opening = !menu.classList.contains('open');
  if (opening) window._closeAllPopovers('thinking');
  menu.classList.toggle('open', opening);
  sel.classList.toggle('open', opening);
}

function setThinkingEffort(level) {
  if (_fnFormActive) {
    _execThinkingEffort = level;
  } else {
    _thinkingEffort = level;
  }
  buildThinkingMenu();
  document.getElementById('thinkingMenu').classList.remove('open');
  document.getElementById('thinkingSelector').classList.remove('open');
}

// ===== Plus menu (+ popover in chat input) =====
// Houses opt-in toggles (Tools today; web search / files in the future).
function togglePlusMenu(e) {
  if (e) e.stopPropagation();
  var menu = document.getElementById('plusMenu');
  var btn = document.getElementById('plusBtn');
  if (!menu) return;
  var opening = !menu.classList.contains('open');
  if (opening) window._closeAllPopovers('plus');
  menu.classList.toggle('open', opening);
  if (btn) btn.classList.toggle('open', opening);
  if (opening) renderPlusMenu();
}

function renderPlusMenu() {
  var toolsItem = document.getElementById('plusMenuTools');
  var check = document.getElementById('plusMenuToolsCheck');
  if (toolsItem) toolsItem.classList.toggle('active', !!window._toolsEnabled);
  if (check) check.innerHTML = window._toolsEnabled
    ? '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path d="M15.188 5.11a.5.5 0 0 1 .752.626l-.056.084-7.5 9a.5.5 0 0 1-.738.033l-3.5-3.5-.064-.078a.501.501 0 0 1 .693-.693l.078.064 3.113 3.113 7.15-8.58z"/></svg>'
    : '';
  _updatePlusBtnIndicator();
}

function _updatePlusBtnIndicator() {
  var btn = document.getElementById('plusBtn');
  if (btn) btn.classList.toggle('has-active', !!window._toolsEnabled);
  _renderActiveToolChips();
}

function _renderActiveToolChips() {
  var host = document.getElementById('activeToolChips');
  if (!host) return;
  var chips = '';
  if (window._toolsEnabled) {
    chips +=
      '<div class="tool-chip" data-tooltip="Tools" onclick="toggleToolsEnabled(event); _updatePlusBtnIndicator();" title="">' +
        '<span class="tool-chip-icon">' +
          '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>' +
        '</span>' +
        '<span class="tool-chip-close" aria-label="Remove">' +
          '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><line x1="3" y1="3" x2="9" y2="9"/><line x1="9" y1="3" x2="3" y2="9"/></svg>' +
        '</span>' +
      '</div>';
  }
  host.innerHTML = chips;
}

function toggleToolsEnabled(e) {
  if (e) e.stopPropagation();
  window._toolsEnabled = !window._toolsEnabled;
  try { localStorage.setItem('agentic_tools_enabled', window._toolsEnabled ? '1' : '0'); } catch (_) {}
  _updatePlusBtnIndicator();
}

// Unified click-outside: close any open popover (thinking / plus / model)
// when the click isn't on its own trigger or panel.
document.addEventListener('click', function(e) {
  var t = e.target;
  if (!t.closest('#plusMenu') && !t.closest('#plusBtn')) {
    var pm = document.getElementById('plusMenu');
    var pb = document.getElementById('plusBtn');
    if (pm) pm.classList.remove('open');
    if (pb) pb.classList.remove('open');
  }
  if (!t.closest('#thinkingMenu') && !t.closest('#thinkingSelector')) {
    var tm = document.getElementById('thinkingMenu');
    var ts = document.getElementById('thinkingSelector');
    if (tm) tm.classList.remove('open');
    if (ts) ts.classList.remove('open');
  }
  if (!t.closest('#modelDropdown') && !t.closest('#modelBadge')) {
    var md = document.getElementById('modelDropdown');
    if (md) md.remove();
  }
  if (!t.closest('#userMenu') && !t.closest('.sidebar-footer')) {
    var um = document.getElementById('userMenu');
    if (um) um.classList.remove('open');
  }
  if (!t.closest('#agentSelector') && !t.closest('#chatAgentBadge') && !t.closest('#execAgentBadge')) {
    var ag = document.getElementById('agentSelector');
    if (ag) ag.remove();
  }
});

(function initPlusMenu() {
  try {
    window._toolsEnabled = localStorage.getItem('agentic_tools_enabled') === '1';
  } catch (_) {
    window._toolsEnabled = false;
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _updatePlusBtnIndicator);
  } else {
    setTimeout(_updatePlusBtnIndicator, 0);
  }
})();

// ===== Detail Panel =====

var _detailNode = null;

function showDetail(node) {
  _detailNode = node;
  selectedPath = node.path;
  var panel = document.getElementById('detailPanel');
  var title = document.getElementById('detailTitle');
  var body = document.getElementById('detailBody');

  panel.classList.remove('collapsed');
  // New dock structure: the panel is a tab inside the right sidebar.
  // Showing node detail should also surface that tab if the sidebar
  // is on another view (or collapsed).
  if (window.rightDock) window.rightDock.show('detail');
  if (title) title.textContent = node.name;

  var statusIcon = node.status === 'success' ? '&#10003;' : node.status === 'error' ? '&#10007;' : '&#9679;';
  var dur = node.duration_ms > 0 ? Math.round(node.duration_ms) + 'ms' : 'running...';

  var html = '<div class="detail-section">' +
    '<div class="detail-section-title">Status</div>' +
    '<div class="detail-badge ' + node.status + '">' + statusIcon + ' ' + node.status + ' &middot; ' + dur + '</div>' +
  '</div>';

  html += '<div class="detail-section">' +
    '<div class="detail-section-title">Path</div>' +
    '<div class="detail-field-value">' + escHtml(node.path) + '</div>' +
  '</div>';

  if (node.prompt) {
    html += '<div class="detail-section">' +
      '<div class="detail-section-title">Prompt / Docstring</div>' +
      '<div class="detail-code">' + escHtml(node.prompt) + '</div>' +
    '</div>';
  }

  if (node.params && Object.keys(node.params).length > 0) {
    var _dp = {};
    for (var _dk in node.params) { if (_dk !== 'runtime' && _dk !== 'callback') _dp[_dk] = node.params[_dk]; }
    if (Object.keys(_dp).length > 0) {
      html += '<div class="detail-section">' +
        '<div class="detail-section-title">Parameters</div>' +
        '<div class="detail-code">' + escHtml(JSON.stringify(_dp, null, 2)) + '</div>' +
      '</div>';
    }
  }

  if (node.output != null) {
    html += '<div class="detail-section">' +
      '<div class="detail-section-title">Output</div>' +
      '<div class="detail-code">' + escHtml(typeof node.output === 'string' ? node.output : JSON.stringify(node.output, null, 2)) + '</div>' +
    '</div>';
  }

  if (node.error) {
    html += '<div class="detail-section">' +
      '<div class="detail-section-title">Error</div>' +
      '<div class="detail-code" style="color:var(--accent-red)">' + escHtml(node.error) + '</div>' +
    '</div>';
  }

  if (node.node_type === 'exec') {
    // Exec nodes show content → reply
    var content = (node.params && node.params._content) || '';
    html += '<div class="detail-section">' +
      '<div class="detail-section-title">LLM Input</div>' +
      '<div class="detail-code">→ ' + escHtml(content) + '</div>' +
    '</div>';
    if (node.raw_reply != null) {
      html += '<div class="detail-section">' +
        '<div class="detail-section-title">LLM Reply</div>' +
        '<div class="detail-code">← ' + escHtml(node.raw_reply) + '</div>' +
      '</div>';
    }
  } else if (node.raw_reply != null) {
    html += '<div class="detail-section">' +
      '<div class="detail-section-title">Raw LLM Reply</div>' +
      '<div class="detail-code">' + escHtml(node.raw_reply) + '</div>' +
    '</div>';
  }

  if (node.attempts && node.attempts.length > 0) {
    html += '<div class="detail-section">' +
      '<div class="detail-section-title">Attempts (' + node.attempts.length + ')</div>' +
      '<div class="detail-code">' + escHtml(JSON.stringify(node.attempts, null, 2)) + '</div>' +
    '</div>';
  }

  html += '<div class="detail-section">' +
    '<div class="detail-section-title">Expose</div>' +
    '<div class="detail-field-value">' + escHtml(node.expose || 'io') + '</div>' +
  '</div>';

  if (node.name !== 'chat_session') {
    html += '<div class="detail-section">' +
      '<button class="rerun-btn" onclick="rerunFromNode(\'' + escAttr(node.path) + '\')">&#8634; Modify ' + escHtml(node.name) + '</button>' +
    '</div>';
  }

  body.innerHTML = html;
}

function closeDetail() {
  _detailNode = null;
  selectedPath = null;
  var panel = document.getElementById('detailPanel');
  panel.style.removeProperty('width');
  panel.classList.add('collapsed');
}

function toggleDetail() {
  var panel = document.getElementById('detailPanel');
  if (!panel.classList.contains('collapsed')) {
    panel.style.removeProperty('width');
  }
  panel.classList.toggle('collapsed');
}

// ===== Code Viewer =====

async function viewSource(name) {
  try {
    var resp = await fetch('/api/function/' + encodeURIComponent(name) + '/source');
    var data = await resp.json();
    if (data.error) {
      console.warn('[viewSource] ' + name + ': ' + data.error);
      return;
    }
    showCodeModal(name, data.source, data.category);
  } catch(e) {
    console.error('[viewSource] ' + name + ':', e);
  }
}

function showCodeModal(name, source, category) {
  var modal = document.getElementById('codeModal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'codeModal';
    modal.className = 'code-modal-overlay';
    modal.innerHTML = '<div class="code-modal">' +
      '<div class="code-modal-header"><span class="code-modal-title" id="codeModalTitle"></span><button class="code-modal-close" onclick="closeCodeModal()">&times;</button></div>' +
      '<div class="code-modal-body"><pre id="codeModalPre"></pre></div>' +
      '<div class="code-modal-actions" id="codeModalActions"></div>' +
    '</div>';
    modal.addEventListener('click', function(e) { if (e.target === modal) closeCodeModal(); });
    document.body.appendChild(modal);
  }
  document.getElementById('codeModalTitle').textContent = name;
  document.getElementById('codeModalPre').innerHTML = highlightPython(source);

  var actions = '<button class="code-modal-btn" onclick="closeCodeModal()">Close</button>';
  if (category !== 'meta') {
    actions += '<button class="code-modal-btn" onclick="editInModal(\'' + escAttr(name) + '\')">Edit</button>';
    actions += '<button class="code-modal-btn" onclick="fixFromModal(\'' + escAttr(name) + '\')">Fix with LLM</button>';
  }
  document.getElementById('codeModalActions').innerHTML = actions;

  requestAnimationFrame(function() { modal.classList.add('active'); });
}

function closeCodeModal() {
  var modal = document.getElementById('codeModal');
  if (modal) modal.classList.remove('active');
}

function editInModal(name) {
  closeCodeModal();
  var input = document.getElementById('chatInput');
  input.value = 'I want to edit function ' + name;
  input.focus();
}

function fixFromModal(name) {
  var instruction = prompt('What should be fixed in ' + name + '?');
  if (!instruction) return;
  closeCodeModal();
  var input = document.getElementById('chatInput');
  input.value = 'fix ' + name + ' ' + instruction;
  sendMessage();
}
