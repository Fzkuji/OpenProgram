// ===== Provider, Agent Settings, Model Management =====

function updateProviderBadge(info) {
  var provBadge = document.getElementById('providerBadge');
  var sessBadge = document.getElementById('sessionBadge');
  if (!provBadge) return;
  if (!info || !info.provider) {
    provBadge.style.display = 'none';
    if (sessBadge) sessBadge.style.display = 'none';
    return;
  }
  var hadSession = _hasActiveSession;
  _hasActiveSession = !!info.session_id;
  provBadge.textContent = info.provider + (info.type ? ' \u00b7 ' + info.type : '') + (_hasActiveSession ? ' \ud83d\udd12' : '');
  provBadge.style.display = '';
  if (hadSession !== _hasActiveSession) loadProviders();
  if (sessBadge) {
    if (info.session_id) {
      var short = info.session_id.split('-').pop() || info.session_id.slice(-8);
      sessBadge.textContent = 'session:' + short;
      sessBadge.title = info.session_id;
      sessBadge.style.display = '';
    } else {
      sessBadge.textContent = 'no session';
      sessBadge.style.display = '';
    }
  }
  loadModelPills();
}

async function loadModelPills() {
  try {
    var resp = await fetch('/api/models');
    var data = await resp.json();
    _modelList = data.models || [];
    _currentModel = data.current || _modelList[0] || '';
  } catch(e) {}
  var badge = document.getElementById('modelBadge');
  if (!badge) return;
  if (!_currentModel && _modelList.length === 0) { badge.style.display = 'none'; return; }
  badge.textContent = _currentModel || '';
  badge.style.display = '';
  if (_hasActiveSession) {
    badge.onclick = null;
    badge.style.cursor = 'not-allowed';
    badge.style.opacity = '0.5';
    badge.title = 'Cannot change model while session is active';
  } else {
    badge.onclick = function(e) { toggleModelDropdown(e); };
    badge.style.cursor = 'pointer';
    badge.style.opacity = '1';
    badge.title = '';
  }
}

function toggleModelDropdown(event) {
  if (event) event.stopPropagation();
  var existing = document.getElementById('modelDropdown');
  if (existing) { existing.remove(); return; }
  var badge = document.getElementById('modelBadge');
  if (!badge || _modelList.length === 0) return;

  var rect = badge.getBoundingClientRect();
  var html = '<div id="modelDropdown" class="model-dropdown" style="top:' +
    (rect.bottom + 4) + 'px;left:' + rect.left + 'px;">';
  for (var i = 0; i < _modelList.length; i++) {
    var m = _modelList[i];
    var cls = m === _currentModel ? 'runtime-badge model active' : 'runtime-badge model';
    html += '<span class="' + cls + '" data-model="' + escAttr(m) + '" style="cursor:pointer">' + escHtml(m) + '</span>';
  }
  html += '</div>';
  document.body.insertAdjacentHTML('beforeend', html);

  var dropdown = document.getElementById('modelDropdown');
  dropdown.addEventListener('click', function(e) {
    var target = e.target.closest('[data-model]');
    if (!target) return;
    e.stopPropagation();
    var model = target.getAttribute('data-model');
    dropdown.remove();
    if (model === _currentModel) return;
    fetch('/api/model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: model, conv_id: currentConvId })
    }).then(function(r) { return r.json(); }).then(function(data) {
      if (data.switched) {
        _currentModel = model;
        badge.textContent = model;
      }
    }).catch(function() {});
  });

  document.addEventListener('click', function closeDropdown(e) {
    var dd = document.getElementById('modelDropdown');
    if (dd && !dd.contains(e.target) && e.target !== badge) {
      dd.remove();
    }
    document.removeEventListener('click', closeDropdown);
  }, { once: false });
}

// ===== Agent Settings =====

async function loadAgentSettings() {
  try {
    var url = '/api/agent_settings';
    if (currentConvId) url += '?conv_id=' + encodeURIComponent(currentConvId);
    var resp = await fetch(url);
    _agentSettings = await resp.json();
  } catch(e) { return; }
  updateAgentBadges();
  // Store thinking configs for both chat and exec
  if (_agentSettings.chat && _agentSettings.chat.thinking) {
    _thinkingConfigChat = _agentSettings.chat.thinking;
  }
  if (_agentSettings.exec && _agentSettings.exec.thinking) {
    _thinkingConfigExec = _agentSettings.exec.thinking;
  }
  // Use whichever is active
  _thinkingConfig = _fnFormActive ? (_thinkingConfigExec || _thinkingConfigChat) : (_thinkingConfigChat || _thinkingConfigExec);
  buildThinkingMenu();
}

function updateAgentBadges() {
  var chatBadge = document.getElementById('chatAgentBadge');
  var execBadge = document.getElementById('execAgentBadge');
  if (chatBadge && _agentSettings.chat) {
    var cp = _agentSettings.chat.provider || '?';
    var cm = _agentSettings.chat.model || '';
    var label = 'Chat: ' + cp + ' \u00b7 ' + cm;
    var sid = _agentSettings.chat.session_id;
    if (sid) {
      label += ' \u00b7 ' + sid.slice(0, 8);
    }
    chatBadge.textContent = label;
    var isLocked = _agentSettings.chat.locked;
    if (isLocked) {
      chatBadge.classList.add('locked');
      chatBadge.onclick = null;
    } else {
      chatBadge.classList.remove('locked');
      chatBadge.onclick = function() { openAgentSelector('chat'); };
    }
  }
  if (execBadge && _agentSettings.exec) {
    var ep = _agentSettings.exec.provider || '?';
    var em = _agentSettings.exec.model || '';
    execBadge.textContent = 'Exec: ' + ep + ' \u00b7 ' + em;
  }
}

function openAgentSelector(agentType) {
  var existing = document.getElementById('agentSelector');
  if (existing) { existing.remove(); return; }

  var badge = document.getElementById(agentType === 'chat' ? 'chatAgentBadge' : 'execAgentBadge');
  if (!badge) return;
  var rect = badge.getBoundingClientRect();

  var current = _agentSettings[agentType] || {};
  var available = _agentSettings.available || {};

  var html = '<div id="agentSelector" class="agent-selector" style="top:' +
    (rect.bottom + 4) + 'px;left:' + Math.max(rect.left - 50, 10) + 'px;">';
  html += '<h4>' + (agentType === 'chat' ? 'Chat Agent' : 'Execution Agent') + '</h4>';

  for (var provName in available) {
    var prov = available[provName];
    html += '<div class="provider-group">';
    html += '<div class="provider-name">' + escHtml(provName) + '</div>';
    var models = prov.models || [];
    if (models.length === 0) models = [prov.default_model || ''];
    for (var i = 0; i < models.length; i++) {
      var m = models[i];
      var isActive = (current.provider === provName && current.model === m);
      var cls = 'model-item' + (isActive ? ' active' : '');
      html += '<button class="' + cls + '" data-provider="' + escAttr(provName) +
              '" data-model="' + escAttr(m) + '">' + escHtml(m) + '</button>';
    }
    html += '</div>';
  }
  html += '</div>';
  document.body.insertAdjacentHTML('beforeend', html);

  var selector = document.getElementById('agentSelector');
  selector.addEventListener('click', function(e) {
    var btn = e.target.closest('[data-provider]');
    if (!btn) return;
    e.stopPropagation();
    var provider = btn.getAttribute('data-provider');
    var model = btn.getAttribute('data-model');
    selector.remove();

    var body = {};
    body[agentType] = { provider: provider, model: model };
    fetch('/api/agent_settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function(r) { return r.json(); }).then(function(data) {
      _agentSettings.chat = data.chat || _agentSettings.chat;
      _agentSettings.exec = data.exec || _agentSettings.exec;
      updateAgentBadges();
      loadAgentSettings();
    }).catch(function() {});
  });

  setTimeout(function() {
    document.addEventListener('click', function closeSelector(e) {
      var sel = document.getElementById('agentSelector');
      if (sel && !sel.contains(e.target) && e.target !== badge) {
        sel.remove();
      }
      document.removeEventListener('click', closeSelector);
    });
  }, 0);
}

// ===== Provider List =====

async function loadProviders() {
  try {
    var resp = await fetch('/api/providers');
    var providers = await resp.json();
    renderProviders(providers);
  } catch(e) {}
}

function renderProviders(providers) {
  var el = document.getElementById('providerList');
  if (!el) return;
  el.innerHTML = providers.map(function(p) {
    var isConfigured = p.configurable ? p.configured : p.available;
    var cls = isConfigured ? 'provider-item configured' : 'provider-item unavailable';
    var typeTag = p.configurable ? 'API' : 'CLI';

    var badgeCls = isConfigured ? 'config-badge configured' : 'config-badge';
    var badgeText = isConfigured ? 'Configured' : 'Set up';
    var configBadge = '<a class="' + badgeCls + '" href="/config" target="_blank" onclick="event.stopPropagation()" title="Configure">' + badgeText + '</a>';

    return '<div class="' + cls + '" title="' + escAttr(p.label) + '">' +
      '<span class="provider-dot"></span>' +
      '<span class="provider-type-tag">' + typeTag + '</span>' +
      '<span class="provider-name">' + escHtml(p.name) + '</span>' +
      configBadge +
    '</div>';
  }).join('');
}

async function switchProvider(name) {
  try {
    var resp = await fetch('/api/provider/' + encodeURIComponent(name), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conv_id: currentConvId })
    });
    var data = await resp.json();
    if (data.switched) {
      loadProviders();
    } else if (data.error) {
      alert('Switch failed: ' + data.error);
    }
  } catch(e) { alert('Switch failed: ' + e.message); }
}
