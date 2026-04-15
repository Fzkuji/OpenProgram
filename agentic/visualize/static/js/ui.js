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

function updateStatus(status) {
  var badge = document.getElementById('statusBadge');
  if (status === 'connected') {
    badge.textContent = 'connected';
    badge.className = 'status-badge';
  } else {
    badge.textContent = 'disconnected';
    badge.className = 'status-badge disconnected';
  }
}

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
  fetch('/api/pause', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function() {
      isPaused = false;
      isRunning = false;
      updateSendBtn();
      addSystemMessage('Execution stopped.');
    })
    .catch(function() {});
}

// ===== Thinking Effort =====

function buildThinkingMenu() {
  // Pick the right config based on mode (exec for fn form, chat for normal)
  var cfg = _fnFormActive ? (_thinkingConfigExec || _thinkingConfigChat) : (_thinkingConfigChat || _thinkingConfigExec);
  _thinkingConfig = cfg;
  if (!cfg) return;
  var menu = document.getElementById('thinkingMenu');
  var label = document.getElementById('thinkingLabel');
  if (!menu || !label) return;

  var currentEffort = _fnFormActive ? _execThinkingEffort : _thinkingEffort;
  var values = cfg.options.map(function(o) { return o.value; });
  if (values.indexOf(currentEffort) < 0) {
    currentEffort = cfg.default || values[0];
    if (_fnFormActive) _execThinkingEffort = currentEffort;
    else _thinkingEffort = currentEffort;
  }
  label.textContent = 'effort: ' + currentEffort;

  menu.innerHTML = cfg.options.map(function(o) {
    var sel = o.value === currentEffort;
    return '<div class="thinking-option' + (sel ? ' selected' : '') + '" onclick="setThinkingEffort(\'' + o.value + '\')">' +
      '<span class="thinking-opt-label">' + o.value + '</span>' +
      '<span class="thinking-opt-desc">' + o.desc + '</span>' +
      '<span class="thinking-opt-check">' + (sel ? '&#10003;' : '') + '</span>' +
    '</div>';
  }).join('');
}

function toggleThinkingMenu(e) {
  e.stopPropagation();
  var menu = document.getElementById('thinkingMenu');
  var sel = document.getElementById('thinkingSelector');
  menu.classList.toggle('open');
  sel.classList.toggle('open', menu.classList.contains('open'));
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

// ===== Detail Panel =====

function showDetail(node) {
  selectedPath = node.path;
  var panel = document.getElementById('detailPanel');
  var title = document.getElementById('detailTitle');
  var body = document.getElementById('detailBody');

  panel.classList.remove('collapsed');
  title.textContent = node.name;

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

  if (node.raw_reply != null) {
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
    '<div class="detail-section-title">Render / Compress</div>' +
    '<div class="detail-field-value">render: ' + escHtml(node.render || 'summary') + ' | compress: ' + (node.compress ? 'true' : 'false') + '</div>' +
  '</div>';

  if (node.name !== 'chat_session') {
    html += '<div class="detail-section">' +
      '<button class="rerun-btn" onclick="rerunFromNode(\'' + escAttr(node.path) + '\')">&#8634; Modify ' + escHtml(node.name) + '</button>' +
    '</div>';
  }

  body.innerHTML = html;
}

function closeDetail() {
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
