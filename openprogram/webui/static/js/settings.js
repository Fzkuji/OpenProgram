// ===== Settings View =====

function toggleUserMenu(event) {
  if (event) event.stopPropagation();
  var menu = document.getElementById('userMenu');
  if (!menu) return;
  menu.classList.toggle('open');

  // Position menu just above the footer with consistent gap
  if (menu.classList.contains('open')) {
    var footer = document.querySelector('.sidebar-footer');
    if (footer) {
      var footerRect = footer.getBoundingClientRect();
      menu.style.bottom = (window.innerHeight - footerRect.top + 8) + 'px';
    }
    setTimeout(function() {
      document.addEventListener('click', _closeUserMenuOnClick);
    }, 0);
  }
}

function _closeUserMenuOnClick(e) {
  var menu = document.getElementById('userMenu');
  if (menu && !menu.contains(e.target)) {
    menu.classList.remove('open');
    document.removeEventListener('click', _closeUserMenuOnClick);
  }
}

function closeUserMenu() {
  var menu = document.getElementById('userMenu');
  if (menu) menu.classList.remove('open');
  document.removeEventListener('click', _closeUserMenuOnClick);
}

function openSettings() {
  closeUserMenu();
  window.location.href = '/settings';
}

function switchSettingsSection(el) {
  document.querySelectorAll('.settings-nav-item').forEach(function(item) {
    item.classList.remove('active');
  });
  el.classList.add('active');
  _loadSettingsSection(el.getAttribute('data-section'));
}

function _loadSettingsSection(section) {
  if (section === 'providers') {
    _loadProvidersSettings();
  } else if (section === 'general') {
    _loadGeneralSettings();
  }
}

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
  var url = _ICON_CDN + slug + '.svg';
  return '<img src="' + url + '" alt="' + escAttr(pid) + '" onerror="this.outerHTML=\'<span class=&quot;provider-icon-letter&quot;>' + letter + '</span>\'">';
}

function _providerIconHtml(pid, size) {
  size = size || 24;
  return '<div class="provider-icon" style="width:' + size + 'px;height:' + size + 'px">' +
         _providerIconInner(pid) + '</div>';
}

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
    : (p.api_key_env ? ('API key env: ' + p.api_key_env) : 'No API key required');

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

  // API key or CLI status
  if (p.api_key_env) {
    html += '<div class="provider-detail-section">';
    html += '<div class="provider-detail-section-title">' +
              '<span>API Key</span>' +
              '<span class="model-count-summary">' + (p.configured ? 'Configured' : 'Not set') + '</span>' +
            '</div>';
    html += '<div class="provider-detail-row">';
    html += '<input class="settings-input" type="password" id="apikey_' + escAttr(p.api_key_env) +
            '" placeholder="' + escAttr(p.api_key_env) + '">';
    html += '<button class="settings-btn" onclick="_saveApiKey(\'' + escAttr(p.api_key_env) + '\', \'' + escAttr(p.id) + '\')">Save</button>';
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
              enabledCount + ' / ' + models.length + ' enabled</span></span>';
    html +=   '<span style="display:flex;gap:6px">' +
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
}

function _modelItemHtml(providerId, m) {
  var caps = [];
  if (m.vision)    caps.push('<span class="cap-badge vision" title="Vision input">👁</span>');
  if (m.tools)     caps.push('<span class="cap-badge tools" title="Tool use">🔧</span>');
  if (m.reasoning) caps.push('<span class="cap-badge reasoning" title="Reasoning / thinking">🧠</span>');
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

// ===== Provider Setup Wizard =====

var _wizardState = null;  // { provider, steps, idx, ctx }

async function openSetupWizard(providerId) {
  try {
    var resp = await fetch('/api/providers/' + encodeURIComponent(providerId) + '/configure');
    if (!resp.ok) {
      alert('No configuration wizard for ' + providerId + ' yet.');
      return;
    }
    var schema = await resp.json();
    _wizardState = { provider: providerId, label: schema.label, description: schema.description || '',
                     steps: schema.steps, idx: 0, ctx: {}, results: [] };
    _renderWizard();
    _runWizardStep();
  } catch(e) {
    alert('Failed to load configuration: ' + e.message);
  }
}

function closeSetupWizard() {
  var o = document.getElementById('setupWizardOverlay');
  if (o) o.remove();
  _wizardState = null;
  // Refresh the providers list so status badges update
  _loadProvidersSettings();
}

function _renderWizard() {
  var existing = document.getElementById('setupWizardOverlay');
  if (existing) existing.remove();

  var s = _wizardState;
  var html = '';
  html += '<div class="code-modal">';
  html += '<div class="code-modal-header">';
  html += '<div class="code-modal-title">Setup: ' + escHtml(s.label) + '</div>';
  html += '<button class="code-modal-close" onclick="closeSetupWizard()">&times;</button>';
  html += '</div>';
  html += '<div class="code-modal-body" style="padding:20px">';
  if (s.description) {
    html += '<div style="color:var(--text-muted);font-size:13px;margin-bottom:16px">' + escHtml(s.description) + '</div>';
  }
  html += '<div id="wizardSteps"></div>';
  html += '</div>';
  html += '</div>';

  var overlay = document.createElement('div');
  overlay.id = 'setupWizardOverlay';
  overlay.className = 'code-modal-overlay active';
  overlay.innerHTML = html;
  document.body.appendChild(overlay);
  _renderWizardSteps();
}

function _renderWizardSteps() {
  var s = _wizardState;
  var container = document.getElementById('wizardSteps');
  if (!container) return;
  var html = '';
  for (var i = 0; i < s.steps.length; i++) {
    var step = s.steps[i];
    var result = s.results[i];
    var icon = '·', color = 'var(--text-muted)';
    if (result) {
      if (result.status === 'ok') { icon = '✓'; color = 'var(--accent-green, #3fb950)'; }
      else if (result.status === 'error') { icon = '✗'; color = 'var(--accent-red, #f85149)'; }
      else if (result.status === 'needs_input') { icon = '?'; color = 'var(--accent-blue, #58a6ff)'; }
    } else if (i === s.idx) {
      icon = '→'; color = 'var(--accent-blue, #58a6ff)';
    }
    html += '<div style="display:flex;align-items:flex-start;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">';
    html += '<div style="color:' + color + ';font-weight:600;min-width:20px">' + icon + '</div>';
    html += '<div style="flex:1">';
    html += '<div style="font-weight:500">' + escHtml(step.label) + '</div>';
    if (result) {
      html += '<div style="color:var(--text-muted);font-size:12px;margin-top:4px">' + escHtml(result.message || '') + '</div>';
      if (result.status === 'error' && result.fix) {
        html += '<div style="margin-top:6px;font-size:12px"><span style="color:var(--text-muted)">Fix: </span><code style="background:var(--bg-tertiary);padding:2px 6px;border-radius:4px">' + escHtml(result.fix) + '</code></div>';
        html += '<div style="margin-top:8px;display:flex;gap:8px">';
        html += '<button class="settings-btn" onclick="_retryWizardStep()">Retry</button>';
        html += '<button class="settings-btn" onclick="closeSetupWizard()">Close</button>';
        html += '</div>';
      }
      if (result.status === 'needs_input') {
        html += _renderWizardInput(i, result);
      }
    }
    html += '</div></div>';
  }
  container.innerHTML = html;
}

function _renderWizardInput(stepIdx, result) {
  var html = '<div style="margin-top:10px">';
  var options = result.options || [];
  if (options.length > 0) {
    html += '<div style="display:flex;flex-direction:column;gap:6px">';
    for (var i = 0; i < options.length; i++) {
      var opt = options[i];
      html += '<label style="display:flex;align-items:center;gap:8px;cursor:pointer;padding:6px 10px;border:1px solid var(--border);border-radius:6px">';
      html += '<input type="radio" name="wizardOpt" value="' + escAttr(opt.value) + '"' + (opt.value === result.default ? ' checked' : '') + '>';
      html += '<span>' + escHtml(opt.value) + '</span>';
      if (opt.desc) html += '<span style="color:var(--text-muted);font-size:12px">— ' + escHtml(opt.desc) + '</span>';
      html += '</label>';
    }
    html += '</div>';
    html += '<button class="settings-btn" style="margin-top:10px" onclick="_submitWizardInput(\'' + escAttr(result.input_key) + '\')">Continue</button>';
  } else {
    html += '<input id="wizardInputField" class="settings-input" type="text" placeholder="' + escAttr(result.default || '') + '">';
    html += '<button class="settings-btn" style="margin-left:8px" onclick="_submitWizardInput(\'' + escAttr(result.input_key) + '\')">Continue</button>';
  }
  html += '</div>';
  return html;
}

function _submitWizardInput(inputKey) {
  var s = _wizardState;
  var picked;
  var radios = document.querySelectorAll('input[name="wizardOpt"]');
  if (radios.length > 0) {
    for (var i = 0; i < radios.length; i++) {
      if (radios[i].checked) { picked = radios[i].value; break; }
    }
  } else {
    var f = document.getElementById('wizardInputField');
    picked = f ? f.value.trim() : '';
  }
  if (!picked) { alert('Please pick a value.'); return; }
  s.ctx[inputKey] = picked;
  // Clear the needs_input result and re-run the same step (it'll now see ctx[inputKey])
  s.results[s.idx] = null;
  _runWizardStep();
}

function _retryWizardStep() {
  _runWizardStep();
}

async function _runWizardStep() {
  var s = _wizardState;
  if (!s || s.idx >= s.steps.length) {
    // All done
    _renderWizardSteps();
    var container = document.getElementById('wizardSteps');
    if (container) {
      container.insertAdjacentHTML('beforeend',
        '<div style="margin-top:16px;padding:12px;background:var(--bg-tertiary);border-radius:6px;color:var(--accent-green,#3fb950)">' +
        'All steps complete. This provider is now configured.</div>' +
        '<div style="margin-top:12px"><button class="settings-btn" onclick="closeSetupWizard()">Done</button></div>'
      );
    }
    return;
  }
  var step = s.steps[s.idx];
  _renderWizardSteps();  // show spinner-ish state for current
  try {
    var resp = await fetch('/api/providers/' + encodeURIComponent(s.provider) +
                           '/configure/step/' + encodeURIComponent(step.id), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(s.ctx),
    });
    var data = await resp.json();
    s.results[s.idx] = data.result;
    s.ctx = data.context || s.ctx;
    _renderWizardSteps();
    if (data.result.status === 'ok') {
      s.idx += 1;
      _runWizardStep();
    }
    // If error or needs_input, stop and wait for user
  } catch(e) {
    s.results[s.idx] = { status: 'error', message: 'Network error: ' + e.message };
    _renderWizardSteps();
  }
}

// ===== General Settings =====
function _loadGeneralSettings() {
  var content = document.getElementById('settingsContent');
  if (!content) return;

  var currentTheme = localStorage.getItem('agentic_theme') || 'dark';

  var html = '';

  // Appearance section
  html += '<div class="settings-section">';
  html += '<h2 class="settings-section-title">Appearance</h2>';
  html += '<div class="settings-card">';
  html += '<div class="settings-row">';
  html += '<div class="settings-label">Color mode</div>';
  html += '<div class="settings-value">';
  html += '<div class="theme-switcher">';
  html += '<button class="theme-btn' + (currentTheme === 'light' ? ' active' : '') + '" onclick="_setTheme(\'light\')">Light</button>';
  html += '<button class="theme-btn' + (currentTheme === 'auto' ? ' active' : '') + '" onclick="_setTheme(\'auto\')">Auto</button>';
  html += '<button class="theme-btn' + (currentTheme === 'dark' ? ' active' : '') + '" onclick="_setTheme(\'dark\')">Dark</button>';
  html += '</div>';
  html += '</div>';
  html += '</div>';
  html += '</div>';
  html += '</div>';

  // Application section
  html += '<div class="settings-section">';
  html += '<h2 class="settings-section-title">Application</h2>';
  html += '<div class="settings-card">';
  html += '<div class="settings-row">';
  html += '<div class="settings-label">Version</div>';
  html += '<div class="settings-value">0.1.0</div>';
  html += '</div>';
  html += '<div class="settings-row">';
  html += '<div class="settings-label">Framework</div>';
  html += '<div class="settings-value">Agentic Programming</div>';
  html += '</div>';
  html += '</div>';
  html += '</div>';

  content.innerHTML = html;
}

function _setTheme(theme) {
  localStorage.setItem('agentic_theme', theme);
  _applyTheme(theme);
  // Update button states
  document.querySelectorAll('.theme-btn').forEach(function(btn) {
    btn.classList.remove('active');
  });
  event.target.classList.add('active');
}

function _applyTheme(theme) {
  if (theme === 'auto') {
    var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.setAttribute('data-theme', prefersDark ? 'dark' : 'light');
  } else {
    document.documentElement.setAttribute('data-theme', theme);
  }
}

// Apply theme on load
(function() {
  var theme = localStorage.getItem('agentic_theme') || 'dark';
  _applyTheme(theme);
  // Listen for system theme changes when on auto
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function() {
    if (localStorage.getItem('agentic_theme') === 'auto') {
      _applyTheme('auto');
    }
  });
})();
