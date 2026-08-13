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

// ===== Providers Settings =====
async function _loadProvidersSettings() {
  var content = document.getElementById('settingsContent');
  if (!content) return;
  content.innerHTML = '<div style="color:var(--text-muted)">Loading...</div>';

  try {
    // Load providers list and current config
    var [provResp, cfgResp, agentResp] = await Promise.all([
      fetch('/api/providers'),
      fetch('/api/config'),
      fetch('/api/agent_settings'),
    ]);
    var providers = await provResp.json();
    var config = await cfgResp.json();
    var agents = await agentResp.json();

    var html = '';

    // Section: Agent Configuration
    html += '<div class="settings-section">';
    html += '<h2 class="settings-section-title">Agent Configuration</h2>';
    html += '<div class="settings-card">';
    html += '<div class="settings-row">';
    html += '<div class="settings-label">Chat Agent</div>';
    html += '<div class="settings-value">' +
            escHtml((agents.chat && agents.chat.provider || '?') + ' / ' + (agents.chat && agents.chat.model || '?')) + '</div>';
    html += '</div>';
    html += '<div class="settings-row">';
    html += '<div class="settings-label">Exec Agent</div>';
    html += '<div class="settings-value">' +
            escHtml((agents.exec && agents.exec.provider || '?') + ' / ' + (agents.exec && agents.exec.model || '?')) + '</div>';
    html += '</div>';
    html += '</div>';
    html += '</div>';

    // Section: LLM Providers
    html += '<div class="settings-section">';
    html += '<h2 class="settings-section-title">LLM Providers</h2>';

    for (var i = 0; i < providers.length; i++) {
      var p = providers[i];
      var isConfigured = p.configurable ? p.configured : p.available;
      var badge = isConfigured
        ? '<span class="settings-badge ok">Available</span>'
        : '<span class="settings-badge missing">Not configured</span>';
      var typeTag = p.configurable ? 'API' : 'CLI';

      html += '<div class="settings-card">';
      html += '<div class="settings-card-header">';
      html += '<div class="settings-card-title">' + escHtml(p.name) + ' <span style="font-size:11px;color:var(--text-muted);font-weight:400">' + typeTag + '</span></div>';
      html += badge;
      html += '</div>';
      if (p.label) {
        html += '<div class="settings-card-desc">' + escHtml(p.label) + '</div>';
      }

      // API key input for configurable providers
      if (p.configurable) {
        var keyName = p.config_key || p.name.toLowerCase();
        var masked = (config.api_keys && config.api_keys[keyName]) || '';
        html += '<div style="display:flex;align-items:center;gap:8px;margin-top:12px">';
        html += '<input class="settings-input" type="password" placeholder="API Key" id="apikey_' + escAttr(keyName) + '" value="' + escAttr(masked) + '">';
        html += '<button class="settings-btn" onclick="_saveApiKey(\'' + escAttr(keyName) + '\')">Save</button>';
        html += '</div>';
      }
      html += '</div>';
    }

    html += '</div>';
    content.innerHTML = html;
  } catch(e) {
    content.innerHTML = '<div style="color:var(--text-muted)">Failed to load: ' + escHtml(e.message) + '</div>';
  }
}

async function _saveApiKey(keyName) {
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
      input.value = value.slice(0, 8) + '...';
      input.type = 'password';
    }
  } catch(e) {}
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
