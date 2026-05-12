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
  'openai': 'openai', 'chatgpt-subscription': 'openai',
  'anthropic': 'claude', 'claude-code': 'claude',
  'google': 'gemini', 'google-vertex': 'gemini',
  'gemini-subscription': 'gemini', 'gemini-cli': 'gemini',
  'gemini-subscription-corp': 'gemini',
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
  var base = 'https://unpkg.com/@lobehub/icons-static-svg@1.4.0/icons/';
  var colorUrl = base + slug + '-color.svg';
  var monoUrl = base + slug + '.svg';
  var letterSpan = '<span class=&quot;provider-icon-letter&quot;>' + letter + '</span>';
  return '<img src="' + colorUrl + '" onerror="if(this.dataset.f){this.outerHTML=\'' + letterSpan + '\'}else{this.dataset.f=1;this.src=\'' + monoUrl + '\'}">';
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
  if (window._closeAllPopovers) window._closeAllPopovers('model');
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

    var body = { model: fullId, session_id: currentSessionId };
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

  // Outside-click close handled by unified popover listener in ui.js
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
  // Reset on provider OR model change: different model within the same provider
  // can have a different thinking_levels list (e.g. gpt-4o → gpt-5). Without
  // resetting we'd keep a level that isn't in the new model's ladder.
  var newChatProv = (_agentSettings.chat && _agentSettings.chat.provider) || null;
  var newChatModel = (_agentSettings.chat && _agentSettings.chat.model) || null;
  if ((_lastChatProvider !== null && newChatProv !== _lastChatProvider)
      || (_lastChatModel !== null && newChatModel !== _lastChatModel)) {
    _thinkingEffort = null;
  }
  _lastChatProvider = newChatProv;
  _lastChatModel = newChatModel;
  var newExecProv = (_agentSettings.exec && _agentSettings.exec.provider) || null;
  var newExecModel = (_agentSettings.exec && _agentSettings.exec.model) || null;
  if ((_lastExecProvider !== null && newExecProv !== _lastExecProvider)
      || (_lastExecModel !== null && newExecModel !== _lastExecModel)) {
    _execThinkingEffort = null;
  }
  _lastExecProvider = newExecProv;
  _lastExecModel = newExecModel;
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
    var detailsParts = [cp];
    if (cm) detailsParts.push(cm);
    var sid = _agentSettings.chat.session_id;
    if (sid) detailsParts.push(sid.slice(0, 8));
    var details = ': ' + detailsParts.join(' \u00b7 ');
    chatBadge.innerHTML = ''
      + '<span class="badge-short">Chat</span>'
      + '<span class="badge-details">' + _escAgentBadge(details) + '</span>';
    chatBadge.title = 'Chat agent' + details;
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
    var execDetailsParts = [ep];
    if (em) execDetailsParts.push(em);
    var execDetails = ': ' + execDetailsParts.join(' \u00b7 ');
    execBadge.innerHTML = ''
      + '<span class="badge-short">Exec</span>'
      + '<span class="badge-details">' + _escAgentBadge(execDetails) + '</span>';
    execBadge.title = 'Execution agent' + execDetails;
  }
  if (typeof refreshTokenBadge === 'function') {
    try { refreshTokenBadge(); } catch (e) {}
  }
}

function _fmtTokens(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000)    return (n / 1000).toFixed(1) + 'K';
  return String(n || 0);
}

// Per-session timestamp of last cache write (ms). Used to determine if
// Anthropic's 5-minute prompt cache TTL has elapsed.
var _cacheWriteTs = {};   // sessionId → Date.now() at last cache write
var _cacheTtlTimer = {};  // sessionId → setTimeout handle

var CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

// Call this whenever a turn completes with cache_write_tokens > 0.
function _recordCacheWrite(sessionId) {
  _cacheWriteTs[sessionId] = Date.now();
  // Clear any pending expiry timer and set a new one.
  if (_cacheTtlTimer[sessionId]) clearTimeout(_cacheTtlTimer[sessionId]);
  _cacheTtlTimer[sessionId] = setTimeout(function() {
    delete _cacheTtlTimer[sessionId];
    // Redraw badge to reflect expired cache.
    if (typeof currentSessionId !== 'undefined' && currentSessionId === sessionId) {
      refreshTokenBadge();
    }
  }, CACHE_TTL_MS);
}
window._recordCacheWrite = _recordCacheWrite;

function _cacheAlive(sessionId) {
  var ts = _cacheWriteTs[sessionId];
  if (!ts) return false;
  return (Date.now() - ts) < CACHE_TTL_MS;
}

// Renders token badge from already-fetched data. Extracted so both the
// WS-driven path (no fetch) and the HTTP-fetch path share the same render logic.
function _renderTokenBadge(data, sessionId) {
  var badge = document.getElementById('tokenBadge');
  if (!badge) return;
  var cur = data.current_tokens || data.naive_sum || 0;
  if (!cur && !data.last_assistant_usage) { badge.style.display = 'none'; return; }
  var win = data.context_window || 0;
  var pct = win ? Math.round((cur / win) * 100) : null;
  var color = 'var(--text-muted)';
  if (pct !== null) {
    if (pct > 85)      color = 'var(--accent-red, #e5534b)';
    else if (pct > 65) color = 'var(--accent-yellow, #d2a106)';
  }
  var cacheRate = Math.round((data.cache_hit_rate || 0) * 100);
  var label = _fmtTokens(cur);
  if (win) label += '/' + _fmtTokens(win) + ' (' + pct + '%)';
  var cacheHtml = '';
  if (data.cache_read_total > 0 || _cacheWriteTs[sessionId]) {
    var alive = _cacheAlive(sessionId);
    var dotColor = alive ? 'var(--accent-blue, #4f8ef7)' : 'var(--text-muted)';
    var cacheStatus = alive ? 'Cache active' : 'Cache expired';
    cacheHtml = ' · <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:' + dotColor + ';vertical-align:middle;margin-bottom:1px" title="' + cacheStatus + '"></span> ' + cacheRate + '%';
  }
  badge.innerHTML = label + cacheHtml;
  badge.style.color = color;
  badge.style.display = '';
  var tip = win
    ? 'Context: ' + cur.toLocaleString() + ' / ' + win.toLocaleString() + ' (' + pct + '%)'
    : 'Context: ' + cur.toLocaleString() + ' tokens';
  if (data.cache_read_total > 0) {
    var ts = _cacheWriteTs[sessionId];
    var remaining = ts ? Math.max(0, Math.round((CACHE_TTL_MS - (Date.now() - ts)) / 1000)) : 0;
    tip += '\nCache: ' + data.cache_read_total.toLocaleString() + ' read (' + cacheRate + '% hit)';
    if (_cacheAlive(sessionId) && remaining > 0) tip += ' · expires in ' + remaining + 's';
    else if (_cacheWriteTs[sessionId]) tip += ' · expired';
  }
  if (data.model) tip += '\nModel: ' + data.model;
  if (data.source_mix) {
    var mix = Object.keys(data.source_mix).map(function(k){return k+': '+data.source_mix[k];}).join(', ');
    if (mix) tip += '\nSources: ' + mix;
  }
  badge.title = tip;
}
window._renderTokenBadge = _renderTokenBadge;

// Branch token stats — fetches /api/sessions/{id}/tokens (used on session
// switch or manual refresh; WS-driven updates skip this via updateTokenBadgeFromWs).
async function refreshTokenBadge() {
  var badge = document.getElementById('tokenBadge');
  if (!badge) return;
  if (typeof currentSessionId === 'undefined' || !currentSessionId) {
    badge.style.display = 'none';
    return;
  }
  try {
    var resp = await fetch('/api/sessions/' + encodeURIComponent(currentSessionId) + '/tokens');
    if (!resp.ok) { badge.style.display = 'none'; return; }
    var data = await resp.json();
    _renderTokenBadge(data, currentSessionId);
  } catch (e) {
    badge.style.display = 'none';
  }
  if (typeof window._refreshBranchTokens === 'function') {
    try { window._refreshBranchTokens(); } catch (e) {}
  }
}
window.refreshTokenBadge = refreshTokenBadge;

function _escAgentBadge(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function openAgentSelector(agentType) {
  var existing = document.getElementById('agentSelector');
  if (existing) {
    var sameType = existing.getAttribute('data-agent') === agentType;
    existing.remove();
    if (sameType) return;  // toggle close
    // else: fall through and open the other one
  }
  if (window._closeAllPopovers) window._closeAllPopovers('agent');

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
  selector.setAttribute('data-agent', agentType);
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

    // The agent-settings update above only writes the agent's
    // *default* model. The session has a per-conv provider/model
    // override that takes priority over the agent default (set by
    // the model picker / inherited from `_user_pinned_*`), so without
    // also pushing this pick through `/api/model` the change has
    // zero effect on the current conversation — the user picked
    // Sonnet here but the chat still answers as Opus because the
    // conv's `model_override` is still `claude-opus-4`. Fire both
    // requests in parallel; the chat side wins because it's the one
    // the runtime resolution actually reads.
    if (agentType === 'chat' && currentSessionId) {
      fetch('/api/model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: provider,
          model: model,
          session_id: currentSessionId,
        }),
      }).catch(function() {});
    }
  });

  // Outside-click close is handled by the unified popover listener in ui.js.
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
