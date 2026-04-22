// ===== Settings View =====

function toggleUserMenu(event) {
  if (event) event.stopPropagation();
  var menu = document.getElementById('userMenu');
  if (!menu) return;
  var opening = !menu.classList.contains('open');
  if (opening && window._closeAllPopovers) window._closeAllPopovers('user');
  menu.classList.toggle('open', opening);
  if (opening) {
    var footer = document.querySelector('.sidebar-footer');
    if (footer) {
      var footerRect = footer.getBoundingClientRect();
      menu.style.bottom = (window.innerHeight - footerRect.top + 8) + 'px';
    }
  }
}

function closeUserMenu() {
  var menu = document.getElementById('userMenu');
  if (menu) menu.classList.remove('open');
}

function openSettings() {
  closeUserMenu();
  if (window.__navigate) { window.__navigate('/settings'); return; }
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
  var body = document.querySelector('.settings-body');
  if (body) body.classList.toggle('providers-active', section === 'providers');
  if (section === 'providers') {
    _loadProvidersSettings();
  } else if (section === 'general') {
    _loadGeneralSettings();
  }
}
