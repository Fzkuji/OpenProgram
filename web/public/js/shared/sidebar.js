// ===== Sidebar: Conversations, Functions, Forms =====

function toggleSidebar() {
  var sb = document.getElementById('sidebar');
  sidebarOpen = !sidebarOpen;
  sb.style.removeProperty('width');
  sb.classList.toggle('collapsed', !sidebarOpen);
  try { localStorage.setItem('sidebarOpen', sidebarOpen ? '1' : '0'); } catch (e) {}
}

// Apply the persisted collapsed state right after AppShell injects the
// sidebar HTML, so a refresh never flips the layout.
function restoreSidebarState() {
  var sb = document.getElementById('sidebar');
  if (!sb) return;
  sb.classList.toggle('collapsed', !sidebarOpen);
}
window.restoreSidebarState = restoreSidebarState;

// ===== Conversations =====

function toggleConvList() {
  var list = document.getElementById('convList');
  var hint = document.getElementById('convHint');
  if (!list) return;
  var hidden = list.style.display === 'none';
  list.style.display = hidden ? '' : 'none';
  if (hint) hint.textContent = hidden ? 'Hide' : 'Show';
}

function toggleFavList() {
  var list = document.getElementById('favList');
  var hint = document.getElementById('favHint');
  if (!list) return;
  var hidden = list.style.display === 'none';
  list.style.display = hidden ? '' : 'none';
  if (hint) hint.textContent = hidden ? 'Hide' : 'Show';
}

function doRefreshFunctions(btn) {
  if (btn.classList.contains('spinning')) return;
  var svg = btn.querySelector('svg');
  if (!svg) return;
  btn.classList.add('spinning');
  refreshFunctions();
  svg.addEventListener('animationend', function handler() {
    svg.removeEventListener('animationend', handler);
    btn.classList.remove('spinning');
    var orig = btn.innerHTML;
    btn.innerHTML = '&#10003;';
    btn.classList.add('done');
    setTimeout(function() {
      btn.innerHTML = orig;
      btn.classList.remove('done');
    }, 800);
  });
}

// Sidebar footer is now a React component (UserMenuFooter) mounted
// into #userMenuFooterMount by AppShell via createPortal. The
// previous toggleUserMenu / openSettings legacy stubs are gone — no
// caller in legacy HTML references them anymore (footer markup was
// also stripped from _sidebar.html).
