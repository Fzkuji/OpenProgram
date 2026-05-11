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

function _setSectionCollapsed(sectionId, listId, hintId, collapsed) {
  var list = document.getElementById(listId);
  var hint = document.getElementById(hintId);
  if (!list) return;
  list.style.display = collapsed ? 'none' : '';
  if (hint) hint.textContent = collapsed ? 'Show' : 'Hide';
  // Mark the surrounding section so CSS can make the "Show" label
  // always visible while collapsed (so users don't think the panel
  // is empty), and leave the "Hide" label hover-only when expanded.
  var section = document.getElementById(sectionId);
  if (section) section.classList.toggle('is-collapsed', collapsed);
}

function toggleConvList() {
  var list = document.getElementById('convList');
  if (!list) return;
  _setSectionCollapsed('convSection', 'convList', 'convHint',
                        list.style.display !== 'none');
}

function toggleFavList() {
  var list = document.getElementById('favList');
  if (!list) return;
  _setSectionCollapsed('favSection', 'favList', 'favHint',
                        list.style.display !== 'none');
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
