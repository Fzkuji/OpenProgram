
// ===== Providers Settings (LobeChat-inspired, Claude palette) =====

var _providersCache = [];

// Provider id → lobehub/icons slug. Unknown ids fall back to first-letter.
var _ICON_SLUGS = {
  'openai': 'openai',
  'openai-codex': 'openai',
  'anthropic': 'claude',
  'google': 'gemini',
  'google-vertex': 'gemini',
  'google-gemini-cli': 'gemini',
  'google-antigravity': 'gemini',
  'azure-openai-responses': 'azure',
  'amazon-bedrock': 'bedrock',
  'openrouter': 'openrouter',
  'groq': 'groq',
  'cerebras': 'cerebras',
  'mistral': 'mistral',
  'minimax': 'minimax',
  'minimax-cn': 'minimax',
  'huggingface': 'huggingface',
  'github-copilot': 'githubcopilot',
  'kimi-coding': 'moonshot',
  'vercel-ai-gateway': 'vercel',
  'opencode': 'opencode',
  'claude-code': 'claude',
  'gemini-cli': 'gemini',
};

var _ICON_CDN = 'https://unpkg.com/@lobehub/icons-static-svg@1.4.0/icons/';
function _providerIconInner(pid) {
  var slug = _ICON_SLUGS[pid];
  var letter = (pid[0] || '?').toUpperCase();
  if (!slug) return '<span class="provider-icon-letter">' + letter + '</span>';
  // Use LobeHub's `-color` variant for full brand color; fall back to mono if
  // the color variant 404s (some providers only ship a mono icon).
  var colorUrl = _ICON_CDN + slug + '-color.svg';
  var monoUrl = _ICON_CDN + slug + '.svg';
  var letterSpan = '<span class=&quot;provider-icon-letter&quot;>' + letter + '</span>';
  return '<img src="' + colorUrl + '" alt="' + escAttr(pid) +
         '" onerror="if(this.dataset.f){this.outerHTML=\'' + letterSpan + '\'}else{this.dataset.f=1;this.src=\'' + monoUrl + '\'}">';
}

function _providerIconHtml(pid, size) {
  size = size || 24;
  return '<div class="provider-icon" style="width:' + size + 'px;height:' + size + 'px">' +
         _providerIconInner(pid) + '</div>';
}

// _CAP_ICONS is defined in providers.js (loaded before this file).

// Small inline icons reused in action buttons (Lucide).
var _EYE_ICON = '<svg class="cap-icon" viewBox="0 0 24 24" stroke="currentColor" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
                '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>' +
                '<circle cx="12" cy="12" r="3"/></svg>';
var _EYE_OFF_ICON = '<svg class="cap-icon" viewBox="0 0 24 24" stroke="currentColor" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
                '<path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/>' +
                '<path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/>' +
                '<path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/>' +
                '<line x1="2" x2="22" y1="2" y2="22"/></svg>';
var _REFRESH_ICON = '<svg class="cap-icon" viewBox="0 0 24 24" stroke="currentColor" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
                '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>' +
                '<path d="M21 3v5h-5"/>' +
                '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>' +
                '<path d="M8 16H3v5"/></svg>';

function _formatCtx(n) {
  if (!n) return '';
  if (n >= 1e6) return (n / 1e6).toFixed(0) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(0) + 'K';
  return String(n);
}

async function _loadProvidersSettings() {
  var content = document.getElementById('settingsContent');
  if (!content) return;
  content.innerHTML =
    '<div class="settings-section">' +
      '<h2 class="settings-section-title">AI Providers</h2>' +
      '<div class="providers-layout">' +
        '<div class="providers-sidebar">' +
          '<div class="providers-search">' +
            '<input id="providerSearchInput" type="search" placeholder="Search providers…" oninput="_filterProvidersList(this.value)">' +
          '</div>' +
          '<div id="providersList"></div>' +
        '</div>' +
        '<div class="providers-detail" id="providerDetail">' +
          '<div class="providers-detail-empty">Select a provider on the left</div>' +
        '</div>' +
      '</div>' +
    '</div>';

  await _renderProvidersList();
}

async function _renderProvidersList(preserveSelection) {
  var listEl = document.getElementById('providersList');
  if (!listEl) return;
  try {
    var resp = await fetch('/api/providers/list');
    var data = await resp.json();
    _providersCache = data.providers || [];
  } catch (e) {
    listEl.innerHTML = '<div style="color:var(--text-muted);padding:10px">Failed: ' + escHtml(e.message) + '</div>';
    return;
  }

  var enabled = _providersCache.filter(function(p) { return p.enabled; });
  var disabled = _providersCache.filter(function(p) { return !p.enabled; });

  var html = '';
  if (enabled.length) {
    html += '<div class="providers-group-label">Enabled</div>';
    enabled.forEach(function(p) { html += _providerItemHtml(p); });
  }
  if (disabled.length) {
    html += '<div class="providers-group-label">Not enabled</div>';
    disabled.forEach(function(p) { html += _providerItemHtml(p); });
  }
  listEl.innerHTML = html;

  // Auto-select the first enabled (or first) provider on initial render.
  if (!preserveSelection) {
    var first = enabled[0] || _providersCache[0];
    if (first) _selectProvider(first.id);
  }
}

function _providerItemHtml(p) {
  var dotClass = p.enabled ? 'on' : (p.configured ? 'off' : 'unconfigured');
  return '<div class="provider-item" data-pid="' + escAttr(p.id) +
         '" data-label="' + escAttr(p.label.toLowerCase()) +
         '" onclick="_selectProvider(\'' + escAttr(p.id) + '\')">' +
         _providerIconHtml(p.id, 24) +
         '<span class="provider-item-label">' + escHtml(p.label) + '</span>' +
         '<span class="provider-item-dot ' + dotClass + '" title="' +
           (p.enabled ? 'Enabled' : (p.configured ? 'Not enabled' : 'Not configured')) + '"></span>' +
         '</div>';
}

function _filterProvidersList(q) {
  q = (q || '').toLowerCase().trim();
  document.querySelectorAll('#providersList .provider-item').forEach(function(el) {
    var label = el.getAttribute('data-label') || '';
    var id = (el.getAttribute('data-pid') || '').toLowerCase();
    el.style.display = (!q || label.indexOf(q) >= 0 || id.indexOf(q) >= 0) ? '' : 'none';
  });
}

async function _selectProvider(pid) {
  document.querySelectorAll('.provider-item').forEach(function(el) { el.classList.remove('active'); });
  var item = document.querySelector('.provider-item[data-pid="' + pid.replace(/"/g, '\\"') + '"]');
  if (item) item.classList.add('active');

  var detail = document.getElementById('providerDetail');
  if (!detail) return;
  detail.innerHTML = '<div class="providers-detail-empty">Loading…</div>';

  var pInfo = (_providersCache || []).find(function(p) { return p.id === pid; });
  if (!pInfo) { detail.innerHTML = '<div class="providers-detail-empty">Unknown provider</div>'; return; }

  var models = [];
  if (pInfo.kind !== 'cli') {
    try {
      var resp = await fetch('/api/providers/' + encodeURIComponent(pid) + '/models');
      var data = await resp.json();
      models = data.models || [];
    } catch (e) { /* leave empty */ }
  }
  _renderProviderDetail(pInfo, models);
}

function _renderProviderDetail(p, models) {
  var detail = document.getElementById('providerDetail');
  if (!detail) return;

  var enabledCount = models.filter(function(m) { return m.enabled; }).length;
  var subtitle = p.kind === 'cli'
    ? ('CLI runtime — binary: ' + (p.cli_binary || '?'))
    : (p.api_key_env ? ('API key env: ' + p.api_key_env) : 'Subscription required');

  var html = '';

  // Header with enable toggle
  html += '<div class="provider-detail-header">';
  html += '<div class="provider-detail-icon">' + _providerIconInner(p.id) + '</div>';
  html += '<div class="provider-detail-title-wrap">';
  html +=   '<div class="provider-detail-title">' + escHtml(p.label) + '</div>';
  html +=   '<div class="provider-detail-subtitle">' + escHtml(subtitle) + '</div>';
  html += '</div>';
  html += '<label class="toggle-switch" title="Enable this provider">' +
            '<input type="checkbox" ' + (p.enabled ? 'checked' : '') +
            ' onchange="_toggleProvider(\'' + escAttr(p.id) + '\', this.checked)">' +
            '<span class="slider"></span>' +
          '</label>';
  html += '</div>';

  // API key + proxy URL + Responses API + connectivity check (for API providers).
  if (p.api_key_env) {
    html += '<div class="provider-detail-section">';
    html += '<div class="provider-detail-section-title">' +
              '<span>API Key</span>' +
              '<span class="model-count-summary">' + (p.configured ? 'Configured' : 'Not set') + '</span>' +
            '</div>';
    html += '<div class="provider-detail-row">';
    html += '<input class="settings-input" type="password" id="apikey_' + escAttr(p.api_key_env) +
            '" placeholder="' + escAttr(p.api_key_env) +
            '" oninput="_onApiKeyInput(\'' + escAttr(p.api_key_env) + '\')">';
    html += '<button class="settings-icon-btn" title="Show/hide" onclick="_toggleKeyVisibility(\'apikey_' + escAttr(p.api_key_env) + '\', \'' + escAttr(p.api_key_env) + '\')">' + _EYE_ICON + '</button>';
    html += '<button class="settings-btn" onclick="_saveApiKey(\'' + escAttr(p.api_key_env) + '\', \'' + escAttr(p.id) + '\')">Save</button>';
    html += '</div>';
    html += '</div>';

    // Custom base URL (API proxy)
    var baseDefault = p.default_base_url ? ('default: ' + p.default_base_url) : '';
    html += '<div class="provider-detail-section">';
    html += '<div class="provider-detail-section-title">' +
              '<span>API Base URL</span>' +
              '<span class="model-count-summary">' + escHtml(baseDefault) + '</span>' +
            '</div>';
    html += '<div class="provider-detail-row">';
    html += '<input class="settings-input" type="text" id="baseurl_' + escAttr(p.id) +
            '" placeholder="' + escAttr(p.default_base_url || 'https://...') +
            '" value="' + escAttr(p.base_url || '') + '">';
    html += '<button class="settings-btn" onclick="_saveProviderBaseUrl(\'' + escAttr(p.id) + '\')">Save</button>';
    html += '</div>';
    html += '</div>';

    // Connectivity check
    html += '<div class="provider-detail-section">';
    html += '<div class="provider-detail-section-title"><span>Connectivity check</span></div>';
    html += '<div class="provider-detail-row">';
    html += '<span class="model-count-summary" style="flex:1">Validates API key + base URL with a tiny PING.</span>';
    html += '<span id="testResult_' + escAttr(p.id) + '" class="test-result"></span>';
    html += '<button class="settings-btn" onclick="_testProvider(\'' + escAttr(p.id) + '\', this)">Check</button>';
    html += '</div>';
    html += '</div>';
  } else if (p.kind === 'cli') {
    html += '<div class="provider-detail-section">';
    html += '<div class="provider-detail-section-title"><span>CLI Binary</span>' +
            '<span class="model-count-summary">' + (p.configured ? 'Found in PATH' : 'Not found') + '</span></div>';
    html += '<div style="color:var(--text-muted);font-size:13px">This provider wraps the <code>' +
            escHtml(p.cli_binary || '') + '</code> CLI. Install it and run its own login command; enable the toggle to use it here.</div>';
    html += '</div>';
  }

  // Model list
  if (p.kind === 'cli') {
    html += '<div class="provider-detail-section">' +
            '<div class="provider-detail-section-title">Models</div>' +
            '<div style="color:var(--text-muted);font-size:13px">CLI runtimes pick their own model per invocation; enabling the provider is enough.</div>' +
            '</div>';
  } else if (models.length) {
    html += '<div class="provider-detail-section">';
    html += '<div class="provider-detail-section-title">';
    html +=   '<span>Models <span class="model-count-summary" id="modelCountSummary">' +
              enabledCount + ' / ' + models.length + ' available</span></span>';
    var fetchBtn = p.supports_fetch
      ? ('<button class="mini-action" onclick="_fetchRemoteModels(\'' + escAttr(p.id) + '\', this)" title="Fetch from ' + escAttr((p.base_url || p.default_base_url || '') + '/models') + '">' + _REFRESH_ICON + '<span style="margin-left:4px">Fetch models</span></button>')
      : '';
    html +=   '<span style="display:flex;gap:6px;align-items:center">' + fetchBtn +
                '<button class="mini-action" onclick="_bulkToggleModels(\'' + escAttr(p.id) + '\', true)">Enable all</button>' +
                '<button class="mini-action" onclick="_bulkToggleModels(\'' + escAttr(p.id) + '\', false)">Disable all</button>' +
              '</span>';
    html += '</div>';
    html += '<div class="model-search"><input type="search" placeholder="Search models…" oninput="_filterModels(this.value)"></div>';
    html += '<div class="model-list" id="modelList">';
    models.forEach(function(m) { html += _modelItemHtml(p.id, m); });
    html += '</div>';
    html += '</div>';
  } else {
    html += '<div class="provider-detail-section"><div style="color:var(--text-muted);font-size:13px">No models in the registry for this provider.</div></div>';
  }

  detail.innerHTML = html;

  // After the detail pane is in the DOM, prefill the API key preview
  // if there's already a value saved for this env var.
  if (p.api_key_env) {
    _loadApiKeyPreview(p.api_key_env);
  }
}

function _modelItemHtml(providerId, m) {
  var caps = [];
  if (m.vision)    caps.push('<span class="cap-badge vision" title="Vision input">' + _CAP_ICONS.vision + '</span>');
  if (m.tools)     caps.push('<span class="cap-badge tools" title="Tool use">' + _CAP_ICONS.tools + '</span>');
  if (m.reasoning) caps.push('<span class="cap-badge reasoning" title="Reasoning / thinking">' + _CAP_ICONS.reasoning + '</span>');
  if (m.context_window) caps.push('<span class="cap-badge ctx">' + _formatCtx(m.context_window) + '</span>');

  return '<div class="model-item" data-name="' + escAttr((m.name || '').toLowerCase()) +
         '" data-id="' + escAttr((m.id || '').toLowerCase()) + '">' +
         '<div class="model-item-icon">' + _providerIconInner(providerId) + '</div>' +
         '<div class="model-item-info">' +
           '<span class="model-item-name">' + escHtml(m.name || m.id) + '</span>' +
           '<span class="model-item-id">' + escHtml(m.id) + '</span>' +
         '</div>' +
         '<div class="model-capabilities">' + caps.join('') + '</div>' +
         '<label class="toggle-switch">' +
           '<input type="checkbox" ' + (m.enabled ? 'checked' : '') +
           ' onchange="_toggleModel(\'' + escAttr(providerId) + '\', \'' + escAttr(m.id) + '\', this.checked)">' +
           '<span class="slider"></span>' +
         '</label>' +
         '</div>';
}

function _filterModels(q) {
  q = (q || '').toLowerCase().trim();
  document.querySelectorAll('#modelList .model-item').forEach(function(el) {
    var name = el.getAttribute('data-name') || '';
    var id = el.getAttribute('data-id') || '';
    el.style.display = (!q || name.indexOf(q) >= 0 || id.indexOf(q) >= 0) ? '' : 'none';
  });
}

async function _toggleProvider(pid, enabled) {
  try {
    await fetch('/api/providers/' + encodeURIComponent(pid) + '/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: enabled }),
    });
  } catch (e) {}
  await _renderProvidersList(true);
  _selectProvider(pid);
}

async function _toggleModel(pid, mid, enabled) {
  try {
    await fetch('/api/providers/' + encodeURIComponent(pid) + '/models/' +
                encodeURIComponent(mid) + '/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: enabled }),
    });
  } catch (e) {}
  // Update just the counter and the sidebar badge; don't rebuild the list (would lose search/scroll).
  try {
    var resp = await fetch('/api/providers/' + encodeURIComponent(pid) + '/models');
    var data = await resp.json();
    var models = data.models || [];
    var total = models.length;
    var enCount = models.filter(function(m) { return m.enabled; }).length;
    var counter = document.getElementById('modelCountSummary');
    if (counter) counter.textContent = enCount + ' / ' + total + ' enabled';
  } catch (e) {}
}

async function _bulkToggleModels(pid, enable) {
  try {
    var resp = await fetch('/api/providers/' + encodeURIComponent(pid) + '/models');
    var data = await resp.json();
    var models = data.models || [];
    var needs = models.filter(function(m) { return !!m.enabled !== !!enable; });
    await Promise.all(needs.map(function(m) {
      return fetch('/api/providers/' + encodeURIComponent(pid) + '/models/' +
                   encodeURIComponent(m.id) + '/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: enable }),
      });
    }));
  } catch (e) {}
  _selectProvider(pid);
}

async function _loadApiKeyPreview(envVar) {
  var input = document.getElementById('apikey_' + envVar);
  if (!input) return;
  try {
    var resp = await fetch('/api/config/key/' + encodeURIComponent(envVar));
    var data = await resp.json();
    if (data.has_value) {
      input.value = data.masked;
      input.dataset.state = 'masked';
    } else {
      input.value = '';
      input.dataset.state = 'empty';
    }
  } catch (e) {}
}

async function _toggleKeyVisibility(inputId, envVar) {
  var el = document.getElementById(inputId);
  if (!el) return;
  var state = el.dataset.state || 'empty';
  if (state === 'empty' || state === 'editing') {
    // Plain password/text swap on a value the user is typing.
    el.type = (el.type === 'password') ? 'text' : 'password';
    return;
  }
  // state === 'masked' or 'revealed' — fetch from server.
  if (state === 'masked') {
    var resp = await fetch('/api/config/key/' + encodeURIComponent(envVar) + '?reveal=1');
    var data = await resp.json();
    if (data.has_value) {
      el.value = data.value;
      el.type = 'text';
      el.dataset.state = 'revealed';
    }
  } else {  // revealed → mask again
    var resp = await fetch('/api/config/key/' + encodeURIComponent(envVar));
    var data = await resp.json();
    if (data.has_value) {
      el.value = data.masked;
      el.type = 'password';
      el.dataset.state = 'masked';
    }
  }
}

function _onApiKeyInput(envVar) {
  var el = document.getElementById('apikey_' + envVar);
  if (!el) return;
  // Clear masked display the moment the user starts typing.
  if (el.dataset.state === 'masked' || el.dataset.state === 'revealed') {
    el.value = '';
    el.type = 'password';
  }
  el.dataset.state = 'editing';
}

async function _saveProviderBaseUrl(pid) {
  var input = document.getElementById('baseurl_' + pid);
  if (!input) return;
  var base = (input.value || '').trim();
  try {
    await fetch('/api/providers/' + encodeURIComponent(pid) + '/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url: base }),
    });
    input.placeholder = base || input.placeholder;
  } catch (e) {}
}

async function _testProvider(pid, btn) {
  var resultEl = document.getElementById('testResult_' + pid);
  if (btn) btn.disabled = true;
  if (resultEl) { resultEl.className = 'test-result'; resultEl.textContent = '…'; }
  try {
    var resp = await fetch('/api/providers/' + encodeURIComponent(pid) + '/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    var data = await resp.json();
    if (resultEl) {
      if (data.ok) {
        resultEl.className = 'test-result ok';
        resultEl.textContent = '✓ ' + (data.latency_ms || 0) + ' ms';
        resultEl.title = 'Tested with ' + (data.model || '');
      } else {
        resultEl.className = 'test-result err';
        resultEl.textContent = '✗ failed';
        resultEl.title = data.error || 'Unknown error';
      }
    }
  } catch (e) {
    if (resultEl) {
      resultEl.className = 'test-result err';
      resultEl.textContent = '✗';
      resultEl.title = e.message;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function _fetchRemoteModels(pid, btn) {
  if (btn) btn.disabled = true;
  try {
    var resp = await fetch('/api/providers/' + encodeURIComponent(pid) + '/fetch-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    var data = await resp.json();
    if (data.error) {
      alert('Fetch failed: ' + data.error);
    } else {
      // Refresh the detail pane to show the newly added models.
      await _renderProvidersList(true);
      _selectProvider(pid);
    }
  } catch (e) {
    alert('Fetch failed: ' + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function _saveApiKey(keyName, providerId) {
  var input = document.getElementById('apikey_' + keyName);
  if (!input) return;
  var value = input.value.trim();
  if (!value || value.indexOf('...') >= 0) return; // Don't save masked values

  try {
    var body = { api_keys: {} };
    body.api_keys[keyName] = value;
    var resp = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    var data = await resp.json();
    if (data.saved) {
      input.value = '';
      input.placeholder = keyName + ' (saved)';
      if (providerId) {
        // Refresh sidebar (configured flag flips) and re-render detail.
        await _renderProvidersList(true);
        _selectProvider(providerId);
      }
    }
  } catch(e) {}
}

