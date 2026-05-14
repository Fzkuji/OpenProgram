async function loadProgramsMeta() {
  try {
    var resp = await fetch('/api/programs/meta');
    var data = await resp.json();
    programsMeta = data || { favorites: [], folders: {} };
  } catch(e) {
    programsMeta = { favorites: [], folders: {} };
  }
}

function renderFunctions() {
  // React owns this rendering now (components/sidebar/favorites-list.tsx).
  // Early return so legacy callers (refreshFunctions, WS functions_list
  // handler, etc.) don't fight the React reconciler by overwriting
  // #favList with innerHTML strings.
  return;
}
function _legacyRenderFunctions_deprecated() {
  var container = document.getElementById('favList');
  var section = document.getElementById('favSection');
  if (!container || !section) return;

  var favSet = new Set(programsMeta.favorites || []);
  var favFiltered = availableFunctions.filter(function(f) { return favSet.has(f.name); });
  var catOrder = ['app', 'generated', 'user', 'meta', 'builtin'];
  var favFns = [];
  for (var ci = 0; ci < catOrder.length; ci++) {
    for (var fi = 0; fi < favFiltered.length; fi++) {
      if ((favFiltered[fi].category || 'user') === catOrder[ci]) favFns.push(favFiltered[fi]);
    }
  }

  if (favFns.length === 0) {
    section.classList.add('empty');
    container.innerHTML = '';
    return;
  }

  section.classList.remove('empty');
  var catIcons = { app: '\u{1F4E6}', meta: '\u{1F6E0}', builtin: '\u2699', generated: '\u2699', user: '\u270E' };
  var html = '';
  for (var i = 0; i < favFns.length; i++) {
    var f = favFns[i];
    var cat = f.category || 'user';
    var icon = catIcons[cat] || '\u270E';
    html += '<div class="fav-item" onclick="clickFunction(\'' + escAttr(f.name) + '\', \'' + escAttr(cat) + '\')" title="' + escAttr(f.description || '') + '">' +
      '<span class="fav-icon">' + icon + '</span>' +
      '<span class="fav-name">' + escHtml(f.name) + '</span>' +
    '</div>';
  }
  container.innerHTML = html;
}

// `refreshFunctions` was migrated to `web/lib/programs-actions.ts`
// (`refreshFunctionsList`) — the React Sidebar's refresh button
// calls it directly. Nothing on the legacy side reads it anymore.

async function deleteFunction(name) {
  if (!confirm('Delete function "' + name + '"?')) return;
  try {
    var resp = await fetch('/api/function/' + encodeURIComponent(name), { method: 'DELETE' });
    var data = await resp.json();
    if (data.deleted) {
      addAssistantMessage('Deleted function "' + name + '".');
      var fResp = await fetch('/api/functions');
      availableFunctions = await fResp.json();
      renderFunctions();
    } else {
      addAssistantMessage('Cannot delete: ' + (data.error || 'unknown error'));
    }
  } catch(e) { alert('Delete failed: ' + e.message); }
}

async function fixFunction(name) {
  var instruction = prompt('What should be fixed in ' + name + '?');
  if (!instruction) return;
  var input = document.getElementById('chatInput');
  input.value = 'fix ' + name + ' ' + instruction;
  sendMessage();
}

// ===== Function Form =====

function _storeState() {
  var s = window.__sessionStore;
  return (s && typeof s.getState === 'function') ? s.getState() : null;
}

function clickFunction(name, category) {
  var fn = availableFunctions.find(function(f) { return f.name === name; });
  if (!fn) return;
  var p = location.pathname;
  var onChat = p === '/chat' || p.indexOf('/s/') === 0;
  if (!onChat) {
    window.__pendingRunFunction = { name: name, cat: category || '' };
    if (window.__navigate) window.__navigate('/chat');
    return;
  }
  var state = _storeState();
  if (state) state.openFnForm(fn);
}

function clickFnExample(fnName) {
  var fn = availableFunctions.find(function(f) { return f.name === fnName; });
  if (!fn) return;
  var state = _storeState();
  if (state) state.openFnForm(fn);
}

function setInput(text) {
  var state = _storeState();
  if (state) {
    if (state.fnFormFunction) state.closeFnForm();
    state.setComposerInput(text);
    state.focusComposer();
  }
}

