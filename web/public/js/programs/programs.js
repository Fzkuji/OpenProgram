/* ==========================================================================
   Programs View — standalone page (settings-like layout)
   ========================================================================== */

var pgAllPrograms = [];
var pgMeta = { favorites: [], folders: {} };
var pgCurrentFolder = '__all__';
var pgViewMode = 'grid';
var pgDraggedProgram = null;

var pgCatIcons = { app: '\u{1F4E6}', meta: '\u{1F6E0}', builtin: '\u2699', generated: '\u2699', user: '\u270E' };
var pgCatLabels = { app: 'Applications', meta: 'Meta Functions', builtin: 'Built-in', generated: 'Generated', user: 'User' };
var pgCatOrder = { app: 0, generated: 1, user: 2, meta: 3, builtin: 4 };

/* ---------- Data ---------- */

async function pgLoadData() {
  try {
    var resp = await Promise.all([
      fetch('/api/functions'),
      fetch('/api/programs/meta')
    ]);
    pgAllPrograms = await resp[0].json();
    pgMeta = await resp[1].json();
  } catch (e) {
    pgAllPrograms = [];
    pgMeta = { favorites: [], folders: {} };
  }
  if (!pgMeta.favorites) pgMeta.favorites = [];
  if (!pgMeta.folders) pgMeta.folders = {};
  pgRender();
}

async function pgSaveMeta() {
  await fetch('/api/programs/meta', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(pgMeta)
  });
  // also refresh sidebar favorites
  if (typeof loadProgramsMeta === 'function') loadProgramsMeta();
}

/* ---------- Helpers ---------- */

function pgEscHtml(s) { var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
function pgEscAttr(s) { return (s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
function pgIsFavorite(name) { return (pgMeta.favorites || []).includes(name); }

function pgFormatDate(ts) {
  if (!ts) return '';
  var diff = Date.now() - ts * 1000;
  if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
  if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
  if (diff < 604800000) return Math.floor(diff / 86400000) + 'd ago';
  return new Date(ts * 1000).toLocaleDateString();
}

function pgGetFolderForProgram(name) {
  for (var key in pgMeta.folders) {
    if (pgMeta.folders[key].includes(name)) return key;
  }
  return null;
}

function pgGetProgramsInFolder(folder) {
  if (folder === '__all__') return pgAllPrograms;
  if (folder === '__uncategorized__') {
    var assigned = new Set();
    for (var key in pgMeta.folders) pgMeta.folders[key].forEach(function(n) { assigned.add(n); });
    return pgAllPrograms.filter(function(p) { return !assigned.has(p.name); });
  }
  if (folder === '__favorites__') {
    var favSet = new Set(pgMeta.favorites);
    return pgAllPrograms.filter(function(p) { return favSet.has(p.name); });
  }
  var nameSet = new Set(pgMeta.folders[folder] || []);
  return pgAllPrograms.filter(function(p) { return nameSet.has(p.name); });
}

/* ---------- Render ---------- */

function pgRender() {
  pgRenderFolders();
  pgRenderContent();
}

function pgRenderFolders() {
  var el = document.getElementById('pgFolderList');
  if (!el) return;
  var html = '';
  var vf = [
    { id: '__all__', name: 'All Programs', icon: '\u{1F4CB}', count: pgAllPrograms.length },
    { id: '__favorites__', name: 'Favorites', icon: '\u2605', count: (pgMeta.favorites || []).length },
    { id: '__uncategorized__', name: 'Uncategorized', icon: '\u{1F4C2}', count: pgGetProgramsInFolder('__uncategorized__').length },
  ];
  for (var i = 0; i < vf.length; i++) {
    var f = vf[i];
    html += '<div class="pg-folder-item' + (pgCurrentFolder === f.id ? ' active' : '') + '" ' +
      'onclick="pgSelectFolder(\'' + f.id + '\')" ' +
      'ondragover="pgFolderDragOver(event)" ondragleave="pgFolderDragLeave(event)" ' +
      'ondrop="pgFolderDrop(event,\'' + pgEscAttr(f.id) + '\')">' +
      '<span class="pg-folder-icon">' + f.icon + '</span>' +
      '<span class="pg-folder-name">' + pgEscHtml(f.name) + '</span>' +
      '<span class="pg-folder-count">' + f.count + '</span></div>';
  }
  html += '<div class="pg-folder-sep"></div>';
  var folderNames = Object.keys(pgMeta.folders).sort();
  for (var j = 0; j < folderNames.length; j++) {
    var name = folderNames[j];
    var count = (pgMeta.folders[name] || []).length;
    html += '<div class="pg-folder-item' + (pgCurrentFolder === name ? ' active' : '') + '" ' +
      'data-folder="' + pgEscAttr(name) + '" ' +
      'onclick="pgSelectFolder(\'' + pgEscAttr(name) + '\')" ' +
      'ondragover="pgFolderDragOver(event)" ondragleave="pgFolderDragLeave(event)" ' +
      'ondrop="pgFolderDrop(event,\'' + pgEscAttr(name) + '\')" ' +
      'oncontextmenu="pgFolderCtx(event,\'' + pgEscAttr(name) + '\')">' +
      '<span class="pg-folder-icon">\u{1F4C1}</span>' +
      '<span class="pg-folder-name">' + pgEscHtml(name) + '</span>' +
      '<span class="pg-folder-count">' + count + '</span></div>';
  }
  html += '<div class="pg-folder-item pg-folder-new" onclick="pgCreateFolder()" title="Create a new folder">' +
    '<span class="pg-folder-icon">+</span>' +
    '<span class="pg-folder-name">New folder</span></div>';
  el.innerHTML = html;
}

function pgRenderContent() {
  var el = document.getElementById('pgContentArea');
  if (!el) return;
  var programs = pgGetProgramsInFolder(pgCurrentFolder);

  // Search
  var searchEl = document.getElementById('pgSearchBox');
  var q = searchEl ? (searchEl.value || '').toLowerCase() : '';
  if (q) programs = programs.filter(function(p) {
    return p.name.toLowerCase().includes(q) || (p.description || '').toLowerCase().includes(q);
  });

  // Filter
  var filterEl = document.getElementById('pgFilterSelect');
  var filter = filterEl ? filterEl.value : 'all';
  if (filter === 'favorites') {
    var favSet = new Set(pgMeta.favorites);
    programs = programs.filter(function(p) { return favSet.has(p.name); });
  } else if (filter !== 'all') {
    programs = programs.filter(function(p) { return p.category === filter; });
  }

  // Sort
  var sortEl = document.getElementById('pgSortSelect');
  var sort = sortEl ? sortEl.value : 'category';
  if (sort === 'recent') programs.sort(function(a, b) { return (b.mtime || 0) - (a.mtime || 0); });
  else if (sort === 'category') programs.sort(function(a, b) { return (pgCatOrder[a.category] || 9) - (pgCatOrder[b.category] || 9); });
  else programs.sort(function(a, b) { return a.name.localeCompare(b.name); });

  if (programs.length === 0) {
    el.innerHTML = '<div class="pg-empty"><div class="pg-empty-icon">\u{1F4C2}</div>' +
      '<div class="pg-empty-text">' + (q ? 'No matching programs' : 'This folder is empty') + '</div>' +
      '<div class="pg-empty-hint">Drag programs here to organize</div></div>';
    return;
  }

  var html = '';
  if (sort === 'category') {
    var groups = {};
    for (var i = 0; i < programs.length; i++) {
      var c = programs[i].category || 'user';
      if (!groups[c]) groups[c] = [];
      groups[c].push(programs[i]);
    }
    var cats = ['app', 'generated', 'user', 'meta', 'builtin'];
    for (var k = 0; k < cats.length; k++) {
      if (!groups[cats[k]]) continue;
      html += '<div class="pg-cat-section"><div class="pg-cat-header">' +
        pgEscHtml(pgCatLabels[cats[k]] || cats[k]) + ' (' + groups[cats[k]].length + ')</div>' +
        '<div class="' + (pgViewMode === 'grid' ? 'pg-grid' : 'pg-list') + '">';
      for (var m = 0; m < groups[cats[k]].length; m++) html += pgRenderCard(groups[cats[k]][m]);
      html += '</div></div>';
    }
  } else {
    html += '<div class="' + (pgViewMode === 'grid' ? 'pg-grid' : 'pg-list') + '">';
    for (var n = 0; n < programs.length; n++) html += pgRenderCard(programs[n]);
    html += '</div>';
  }
  el.innerHTML = html;
}

function pgRenderCard(p) {
  var cat = p.category || 'user';
  var fav = pgIsFavorite(p.name);
  var desc = p.description ? p.description.split('.')[0] : '';
  var folder = pgGetFolderForProgram(p.name);
  return '<div class="pg-card" draggable="true" ' +
    'ondragstart="pgDragStart(event,\'' + pgEscAttr(p.name) + '\')" ' +
    'onclick="pgRunProgram(\'' + pgEscAttr(p.name) + '\',\'' + pgEscAttr(cat) + '\')" ' +
    'oncontextmenu="pgProgramCtx(event,\'' + pgEscAttr(p.name) + '\')">' +
    '<div class="pg-card-icon cat-' + cat + '">' + (pgCatIcons[cat] || '\u270E') + '</div>' +
    '<div class="pg-card-info">' +
      '<div class="pg-card-name">' + pgEscHtml(p.name) + '</div>' +
      '<div class="pg-card-desc">' + pgEscHtml(desc) + '</div>' +
      '<div class="pg-card-meta">' + pgEscHtml(cat) +
        (folder ? ' \u00b7 \u{1F4C1} ' + pgEscHtml(folder) : '') +
        ' \u00b7 ' + pgFormatDate(p.mtime) + '</div>' +
    '</div>' +
    '<button class="pg-fav-btn ' + (fav ? 'favorited' : '') + '" onclick="pgToggleFav(\'' + pgEscAttr(p.name) + '\',event)">' +
      (fav ? '\u2605' : '\u2606') + '</button></div>';
}

/* ---------- Actions ---------- */

function pgRunProgram(name, category) {
  var url = '/chat?run=' + encodeURIComponent(name) + '&cat=' + encodeURIComponent(category);
  if (window.__navigate) { window.__navigate(url); return; }
  window.location.href = url;
}

function pgEditProgram(name) {
  var url = '/chat?run=edit&fn=' + encodeURIComponent(name);
  if (window.__navigate) { window.__navigate(url); return; }
  window.location.href = url;
}

function pgToggleView() {
  pgViewMode = pgViewMode === 'grid' ? 'list' : 'grid';
  var btn = document.getElementById('pgViewToggle');
  if (btn) btn.textContent = pgViewMode === 'grid' ? 'List' : 'Grid';
  pgRender();
}

async function pgToggleFav(name, e) {
  e.stopPropagation();
  var idx = pgMeta.favorites.indexOf(name);
  if (idx >= 0) pgMeta.favorites.splice(idx, 1);
  else pgMeta.favorites.push(name);
  await pgSaveMeta();
  pgRender();
}

function pgSelectFolder(id) { pgCurrentFolder = id; pgRender(); }

/* ---------- Folder CRUD ---------- */

function pgCreateFolder() {
  var list = document.getElementById('pgFolderList');
  if (!list) return;
  var item = document.createElement('div');
  item.className = 'pg-folder-item active';
  item.innerHTML = '<span class="pg-folder-icon">\u{1F4C1}</span>' +
    '<input class="pg-rename-input" type="text" placeholder="New folder" autofocus>';
  // Insert the input row above the "+ New folder" button so the button stays at bottom.
  var newFolderBtn = list.querySelector('.pg-folder-new');
  if (newFolderBtn) list.insertBefore(item, newFolderBtn);
  else list.appendChild(item);
  var input = item.querySelector('input');
  input.focus();

  function commit() {
    var name = input.value.trim();
    if (!name) { item.remove(); return; }
    if (pgMeta.folders[name]) { input.style.borderColor = 'var(--accent-red)'; return; }
    pgMeta.folders[name] = [];
    pgCurrentFolder = name;
    pgSaveMeta().then(function() { pgRender(); });
  }
  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { item.remove(); }
  });
  input.addEventListener('blur', commit);
}

function pgRenameFolder(oldName) {
  var items = document.querySelectorAll('.pg-folder-item[data-folder]');
  var item = null;
  for (var i = 0; i < items.length; i++) {
    if (items[i].dataset.folder === oldName) { item = items[i]; break; }
  }
  if (!item) return;
  var nameEl = item.querySelector('.pg-folder-name');
  if (!nameEl) return;
  var input = document.createElement('input');
  input.className = 'pg-rename-input';
  input.type = 'text';
  input.value = oldName;
  nameEl.style.display = 'none';
  nameEl.parentNode.insertBefore(input, nameEl.nextSibling);
  item.removeAttribute('onclick');
  item.removeAttribute('oncontextmenu');

  var done = false;
  function commit() {
    if (done) return;
    done = true;
    var newName = input.value.trim();
    if (!newName || newName === oldName) { pgRender(); return; }
    if (pgMeta.folders[newName]) { done = false; input.style.borderColor = 'var(--accent-red)'; return; }
    pgMeta.folders[newName] = pgMeta.folders[oldName] || [];
    delete pgMeta.folders[oldName];
    if (pgCurrentFolder === oldName) pgCurrentFolder = newName;
    pgSaveMeta().then(function() { pgRender(); });
  }
  input.addEventListener('keydown', function(e) {
    e.stopPropagation();
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { done = true; pgRender(); }
  });
  input.addEventListener('blur', function() { setTimeout(commit, 50); });
  setTimeout(function() { input.focus(); input.select(); }, 100);
}

function pgDeleteFolder(name) {
  if (!confirm('Delete folder "' + name + '"? Programs will be moved to Uncategorized.')) return;
  delete pgMeta.folders[name];
  if (pgCurrentFolder === name) pgCurrentFolder = '__all__';
  pgSaveMeta().then(function() { pgRender(); });
}

/* ---------- Drag & Drop ---------- */

function pgDragStart(e, name) {
  pgDraggedProgram = name;
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', name);
}

function pgFolderDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  e.currentTarget.classList.add('drag-over');
}

function pgFolderDragLeave(e) { e.currentTarget.classList.remove('drag-over'); }

async function pgFolderDrop(e, folder) {
  e.preventDefault();
  e.currentTarget.classList.remove('drag-over');
  if (!pgDraggedProgram) return;
  var name = pgDraggedProgram;
  pgDraggedProgram = null;
  for (var key in pgMeta.folders) {
    var arr = pgMeta.folders[key];
    var idx = arr.indexOf(name);
    if (idx >= 0) arr.splice(idx, 1);
  }
  if (folder !== '__all__' && folder !== '__uncategorized__' && folder !== '__favorites__') {
    if (!pgMeta.folders[folder]) pgMeta.folders[folder] = [];
    pgMeta.folders[folder].push(name);
  }
  await pgSaveMeta();
  pgRender();
}

/* ---------- Context Menus ---------- */

function pgRemoveCtx() { var m = document.getElementById('pgCtxMenu'); if (m) m.remove(); }

function pgShowCtx(e, items) {
  pgRemoveCtx();
  var menu = document.createElement('div');
  menu.id = 'pgCtxMenu';
  menu.className = 'pg-ctx-menu';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';
  for (var i = 0; i < items.length; i++) {
    var it = items[i];
    if (it === '---') {
      var sep = document.createElement('div');
      sep.className = 'pg-ctx-sep';
      menu.appendChild(sep);
      continue;
    }
    var div = document.createElement('div');
    div.className = 'pg-ctx-item';
    div.textContent = it.label;
    (function(action) {
      div.addEventListener('click', function() { pgRemoveCtx(); action(); });
    })(it.action);
    menu.appendChild(div);
  }
  document.body.appendChild(menu);
  var r = menu.getBoundingClientRect();
  if (r.right > window.innerWidth) menu.style.left = (window.innerWidth - r.width - 4) + 'px';
  if (r.bottom > window.innerHeight) menu.style.top = (window.innerHeight - r.height - 4) + 'px';
}

document.addEventListener('click', pgRemoveCtx);

function pgProgramCtx(e, name) {
  e.preventDefault(); e.stopPropagation();
  var fav = pgIsFavorite(name);
  var items = [
    { label: fav ? '\u2605 Unfavorite' : '\u2606 Favorite', action: function() { pgToggleFav(name, { stopPropagation: function(){} }); } },
    { label: '\u270E Edit...', action: function() { pgEditProgram(name); } },
    '---',
  ];
  var folders = Object.keys(pgMeta.folders).sort();
  for (var i = 0; i < folders.length; i++) {
    (function(f) {
      items.push({ label: '\u{1F4C1} Move to ' + f, action: async function() {
        for (var key in pgMeta.folders) { var arr = pgMeta.folders[key]; var idx = arr.indexOf(name); if (idx >= 0) arr.splice(idx, 1); }
        pgMeta.folders[f].push(name);
        await pgSaveMeta(); pgRender();
      }});
    })(folders[i]);
  }
  if (pgGetFolderForProgram(name)) {
    items.push({ label: '\u{1F4C2} Remove from folder', action: async function() {
      for (var key in pgMeta.folders) { var arr = pgMeta.folders[key]; var idx = arr.indexOf(name); if (idx >= 0) arr.splice(idx, 1); }
      await pgSaveMeta(); pgRender();
    }});
  }
  pgShowCtx(e, items);
}

function pgFolderCtx(e, name) {
  e.preventDefault(); e.stopPropagation();
  pgShowCtx(e, [
    { label: 'Rename', action: function() { pgRenameFolder(name); } },
    { label: 'Delete', action: function() { pgDeleteFolder(name); } },
    '---',
    { label: '\u{1F4C1} New folder', action: function() { pgCreateFolder(); } },
  ]);
}

function pgSidebarCtx(e) {
  if (e.target.closest('.pg-folder-item')) return;
  e.preventDefault();
  pgShowCtx(e, [
    { label: '\u{1F4C1} New folder', action: function() { pgCreateFolder(); } },
  ]);
}

function pgContentCtx(e) {
  if (e.target.closest('.pg-card')) return;
  e.preventDefault();
  pgShowCtx(e, [
    { label: '\u{1F4C1} New folder', action: function() { pgCreateFolder(); } },
  ]);
}
