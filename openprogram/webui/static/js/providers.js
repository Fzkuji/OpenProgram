// ===== Provider, Agent Settings, Model Management =====

// Inline Lucide-style capability icons (linear, currentColor stroke).
// Shared with settings.js.
var _CAP_ICONS = {
  vision:    '<svg class="cap-icon" viewBox="0 0 24 24" stroke="currentColor">' +
             '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>' +
             '<circle cx="12" cy="12" r="3"/></svg>',
  tools:     '<svg class="cap-icon" viewBox="0 0 24 24" stroke="currentColor">' +
             '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>' +
             '</svg>',
  reasoning: '<svg class="cap-icon" viewBox="0 0 24 24" stroke="currentColor">' +
             '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.582a.5.5 0 0 1 0 .962L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/>' +
             '<path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/></svg>',
};

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

// Enabled model catalog (across providers). Populated by loadModelPills().
var _enabledModels = [];

async function loadModelPills() {
  // 1. Fetch which models the user has enabled across all providers.
  try {
    var gResp = await fetch('/api/models/enabled');
    var gData = await gResp.json();
    _enabledModels = gData.models || [];
  } catch (e) { _enabledModels = []; }

  // 2. Current-provider model list stays available for backward compat.
  try {
    var resp = await fetch('/api/models');
    var data = await resp.json();
    _modelList = data.models || [];
    _currentModel = data.current || _modelList[0] || '';
  } catch (e) {}

  var badge = document.getElementById('modelBadge');
  if (!badge) return;
  if (!_currentModel && _enabledModels.length === 0 && _modelList.length === 0) {
    badge.style.display = 'none';
    return;
  }
  badge.textContent = _currentModel || 'Select model';
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

// Provider id → lobehub icon slug (mirror of settings.js table).
var _MODEL_ICON_SLUGS = {
  'openai': 'openai', 'openai-codex': 'openai',
  'anthropic': 'claude', 'claude-code': 'claude',
  'google': 'gemini', 'google-vertex': 'gemini',
  'google-gemini-cli': 'gemini', 'gemini-cli': 'gemini',
  'google-antigravity': 'gemini',
  'azure-openai-responses': 'azure',
  'amazon-bedrock': 'bedrock',
  'openrouter': 'openrouter',
  'groq': 'groq', 'cerebras': 'cerebras', 'mistral': 'mistral',
  'minimax': 'minimax', 'minimax-cn': 'minimax',
  'huggingface': 'huggingface',
  'github-copilot': 'githubcopilot',
  'kimi-coding': 'moonshot',
  'vercel-ai-gateway': 'vercel',
  'opencode': 'opencode',
};

function _dropdownProviderIcon(pid) {
  var slug = _MODEL_ICON_SLUGS[pid];
  var letter = (pid[0] || '?').toUpperCase();
  if (!slug) return '<span class="provider-icon-letter">' + letter + '</span>';
  var url = 'https://unpkg.com/@lobehub/icons-static-svg@1.4.0/icons/' + slug + '.svg';
  return '<img src="' + url + '" onerror="this.outerHTML=\'<span class=&quot;provider-icon-letter&quot;>' + letter + '</span>\'">';
}

function _fmtCtxShort(n) {
  if (!n) return '';
  if (n >= 1e6) return (n / 1e6).toFixed(0) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(0) + 'K';
  return String(n);
}

function toggleModelDropdown(event) {
  if (event) event.stopPropagation();
  var existing = document.getElementById('modelDropdown');
  if (existing) { existing.remove(); return; }
  var badge = document.getElementById('modelBadge');
  if (!badge) return;

  // Prefer the catalog if the user has set up any enabled models.
  // Otherwise fall back to the current provider's list (back-compat).
  var useCatalog = _enabledModels.length > 0;
  if (!useCatalog && _modelList.length === 0) return;

  var rect = badge.getBoundingClientRect();
  var dd = document.createElement('div');
  dd.id = 'modelDropdown';
  dd.className = 'model-dropdown';
  dd.style.top = (rect.bottom + 4) + 'px';
  dd.style.left = rect.left + 'px';

  var html = '';

  if (useCatalog) {
    // Group by provider_label
    var byProv = {};
    var order = [];
    _enabledModels.forEach(function(m) {
      var key = m.provider || '?';
      if (!byProv[key]) { byProv[key] = { label: m.provider_label || key, items: [] }; order.push(key); }
      byProv[key].items.push(m);
    });

    order.forEach(function(pid) {
      var group = byProv[pid];
      html += '<div class="model-dd-group-label">' +
                '<span class="provider-icon" style="width:14px;height:14px">' + _dropdownProviderIcon(pid) + '</span>' +
                '<span>' + escHtml(group.label) + '</span>' +
              '</div>';
      group.items.forEach(function(m) {
        var full = pid + ':' + m.id;
        var active = (full === _currentModel || m.id === _currentModel);
        var caps = '';
        if (m.vision)    caps += '<span class="cap-badge vision" title="Vision">' + _CAP_ICONS.vision + '</span>';
        if (m.tools)     caps += '<span class="cap-badge tools" title="Tools">' + _CAP_ICONS.tools + '</span>';
        if (m.reasoning) caps += '<span class="cap-badge reasoning" title="Reasoning">' + _CAP_ICONS.reasoning + '</span>';
        if (m.context_window) caps += '<span class="cap-badge ctx">' + _fmtCtxShort(m.context_window) + '</span>';

        html += '<div class="model-dd-item' + (active ? ' active' : '') +
                '" data-model="' + escAttr(full) + '" data-provider="' + escAttr(pid) + '">' +
                  '<span class="model-dd-name">' + escHtml(m.name || m.id) + '</span>' +
                  '<span class="model-dd-caps">' + caps + '</span>' +
                '</div>';
      });
    });
  } else {
    // Legacy fallback: flat list for the current provider.
    html += '<div class="model-dd-group-label"><span>Models</span></div>';
    _modelList.forEach(function(m) {
      var active = (m === _currentModel);
      html += '<div class="model-dd-item' + (active ? ' active' : '') +
              '" data-model="' + escAttr(m) + '">' +
                '<span class="model-dd-name">' + escHtml(m) + '</span>' +
              '</div>';
    });
  }

  dd.innerHTML = html;
  document.body.appendChild(dd);

  dd.addEventListener('click', function(e) {
    var target = e.target.closest('[data-model]');
    if (!target) return;
    e.stopPropagation();
    var fullId = target.getAttribute('data-model');
    var targetProvider = target.getAttribute('data-provider');  // may be null
    dd.remove();
    if (fullId === _currentModel) return;

    var body = { model: fullId, conv_id: currentConvId };
    if (targetProvider) body.provider = targetProvider;

    fetch('/api/model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function(r) { return r.json(); }).then(function(data) {
      if (data.switched) {
        _currentModel = fullId;
        badge.textContent = fullId;
      }
    }).catch(function() {});
  });

  document.addEventListener('click', function closeDropdown(e) {
    var dd2 = document.getElementById('modelDropdown');
    if (dd2 && !dd2.contains(e.target) && e.target !== badge) dd2.remove();
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

async function openAgentSelector(agentType) {
  var existing = document.getElementById('agentSelector');
  if (existing) { existing.remove(); return; }

  var badge = document.getElementById(agentType === 'chat' ? 'chatAgentBadge' : 'execAgentBadge');
  if (!badge) return;

  // Source of truth: models the user enabled in Settings.
  var catalog = [];
  try {
    var resp = await fetch('/api/models/enabled');
    var data = await resp.json();
    catalog = data.models || [];
  } catch (e) { catalog = []; }

  // Fallback: if nothing's enabled yet, fall back to the legacy
  // _agentSettings.available map so the user isn't locked out on first use.
  var legacyMode = catalog.length === 0;

  var current = _agentSettings[agentType] || {};
  var rect = badge.getBoundingClientRect();

  var selector = document.createElement('div');
  selector.id = 'agentSelector';
  selector.className = 'agent-selector model-dropdown';
  selector.style.top = (rect.bottom + 4) + 'px';
  selector.style.left = Math.max(rect.left - 50, 10) + 'px';

  var html = '';
  html += '<div class="model-dd-group-label" style="padding-top:6px">' +
            '<span>' + (agentType === 'chat' ? 'Chat Agent' : 'Execution Agent') + '</span>' +
          '</div>';

  if (!legacyMode) {
    // Group by provider using icons + capability badges.
    var byProv = {};
    var order = [];
    catalog.forEach(function(m) {
      var key = m.provider || '?';
      if (!byProv[key]) { byProv[key] = { label: m.provider_label || key, items: [] }; order.push(key); }
      byProv[key].items.push(m);
    });

    order.forEach(function(pid) {
      var group = byProv[pid];
      html += '<div class="model-dd-group-label">' +
                '<span class="provider-icon" style="width:14px;height:14px">' + _dropdownProviderIcon(pid) + '</span>' +
                '<span>' + escHtml(group.label) + '</span>' +
              '</div>';
      group.items.forEach(function(m) {
        var active = (current.provider === pid && (current.model === m.id || current.model === pid + ':' + m.id));
        var caps = '';
        if (m.vision)    caps += '<span class="cap-badge vision" title="Vision">' + _CAP_ICONS.vision + '</span>';
        if (m.tools)     caps += '<span class="cap-badge tools" title="Tools">' + _CAP_ICONS.tools + '</span>';
        if (m.reasoning) caps += '<span class="cap-badge reasoning" title="Reasoning">' + _CAP_ICONS.reasoning + '</span>';
        if (m.context_window) caps += '<span class="cap-badge ctx">' + _fmtCtxShort(m.context_window) + '</span>';

        html += '<div class="model-dd-item' + (active ? ' active' : '') +
                '" data-provider="' + escAttr(pid) +
                '" data-model="' + escAttr(m.id) + '">' +
                  '<span class="model-dd-name">' + escHtml(m.name || m.id) + '</span>' +
                  '<span class="model-dd-caps">' + caps + '</span>' +
                '</div>';
      });
    });

    html += '<div class="model-dd-group-label" style="padding-top:10px;font-size:11px">' +
              '<a href="/settings" style="color:var(--accent-blue);text-decoration:none">Manage models in Settings →</a>' +
            '</div>';
  } else {
    // Legacy fallback (no enabled models yet).
    var available = _agentSettings.available || {};
    for (var provName in available) {
      var prov = available[provName];
      html += '<div class="model-dd-group-label">' +
                '<span class="provider-icon" style="width:14px;height:14px">' + _dropdownProviderIcon(provName) + '</span>' +
                '<span>' + escHtml(provName) + '</span>' +
              '</div>';
      var models = prov.models || [];
      if (models.length === 0) models = [prov.default_model || ''];
      models.forEach(function(m) {
        var active = (current.provider === provName && current.model === m);
        html += '<div class="model-dd-item' + (active ? ' active' : '') +
                '" data-provider="' + escAttr(provName) +
                '" data-model="' + escAttr(m) + '">' +
                  '<span class="model-dd-name">' + escHtml(m) + '</span>' +
                '</div>';
      });
    }
    html += '<div class="model-dd-group-label" style="padding-top:10px;font-size:11px">' +
              '<a href="/settings" style="color:var(--accent-blue);text-decoration:none">Enable models in Settings →</a>' +
            '</div>';
  }

  selector.innerHTML = html;
  document.body.appendChild(selector);

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
