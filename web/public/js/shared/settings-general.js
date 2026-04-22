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
