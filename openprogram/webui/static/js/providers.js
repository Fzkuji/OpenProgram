// ===== Provider, Agent Settings, Model Management =====

// SVG icon paths (24x24 viewBox, stroke-based)
var _CAP_ICONS = {
  vision: '<svg class="cap-icon cap-vision" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" title="Vision"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
  video:  '<svg class="cap-icon cap-video"  viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" title="Video"><rect x="2" y="6" width="14" height="12" rx="2"/><path d="M16 10l6-3v10l-6-3"/></svg>',
  tools:  '<svg class="cap-icon cap-tools"  viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" title="Tool use"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>',
  reasoning: '<svg class="cap-icon cap-reasoning" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" title="Reasoning"><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/></svg>',
};

function _modelCapIcons(caps) {
  var html = '<span class="model-cap-icons">';
  if (caps.vision)    html += _CAP_ICONS.vision;
  if (caps.video)     html += _CAP_ICONS.video;
  if (caps.tools)     html += _CAP_ICONS.tools;
  if (caps.reasoning) html += _CAP_ICONS.reasoning;
  html += '</span>';
  return html;
}


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
      body: JSON.stringify({ model: model, session_id: currentSessionId })
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
    if (currentSessionId) url += '?session_id=' + encodeURIComponent(currentSessionId);
    var resp = await fetch(url);
    _agentSettings = await resp.json();
  } catch(e) { return; }
  updateAgentBadges();
  // Provider change detection: if the chat or exec provider differs from last
  // load, reset the corresponding effort so buildThinkingMenu picks the new
  // provider's default. Otherwise a value like "xhigh" (valid for both codex
  // and claude) would silently persist across switches instead of reverting
  // to each provider's configured default.
  var newChatProv = (_agentSettings.chat && _agentSettings.chat.provider) || null;
  if (_lastChatProvider !== null && newChatProv !== _lastChatProvider) {
    _thinkingEffort = null;
  }
  _lastChatProvider = newChatProv;
  var newExecProv = (_agentSettings.exec && _agentSettings.exec.provider) || null;
  if (_lastExecProvider !== null && newExecProv !== _lastExecProvider) {
    _execThinkingEffort = null;
  }
  _lastExecProvider = newExecProv;
  if (_agentSettings.chat && _agentSettings.chat.thinking) {
    _thinkingConfig = _agentSettings.chat.thinking;
    buildThinkingMenu();
  }
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
    var modelCaps = prov.model_caps || {};
    for (var i = 0; i < models.length; i++) {
      var m = models[i];
      var isActive = (current.provider === provName && current.model === m);
      var cls = 'model-item' + (isActive ? ' active' : '');
      var caps = modelCaps[m] || {};
      var iconsHtml = _modelCapIcons(caps);
      html += '<button class="' + cls + '" data-provider="' + escAttr(provName) +
              '" data-model="' + escAttr(m) + '">' +
              '<span class="model-item-name">' + escHtml(m) + '</span>' +
              iconsHtml + '</button>';
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
      body: JSON.stringify({ session_id: currentSessionId })
    });
    var data = await resp.json();
    if (data.switched) {
      loadProviders();
    } else if (data.error) {
      alert('Switch failed: ' + data.error);
    }
  } catch(e) { alert('Switch failed: ' + e.message); }
}
