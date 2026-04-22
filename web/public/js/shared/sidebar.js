// ===== Sidebar: Conversations, Functions, Forms =====

function toggleSidebar() {
  var sb = document.getElementById('sidebar');
  sidebarOpen = !sidebarOpen;
  sb.style.removeProperty('width');
  sb.classList.toggle('collapsed', !sidebarOpen);
}

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
