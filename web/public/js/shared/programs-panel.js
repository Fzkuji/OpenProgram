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
  var maxShow = 4;
  var html = '';
  for (var i = 0; i < Math.min(favFns.length, maxShow); i++) {
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

async function refreshFunctions() {
  try {
    var resp = await fetch('/api/functions');
    availableFunctions = await resp.json();
    renderFunctions();
  } catch(e) { console.error('Refresh failed:', e); }
}

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

function clickFunction(name, category) {
  var fn = availableFunctions.find(function(f) { return f.name === name; });
  if (fn) showFnForm(fn);
}

function clickFnExample(fnName) {
  var fn = availableFunctions.find(function(f) { return f.name === fnName; });
  if (fn) {
    showFnForm(fn);
  } else {
    setInput('run ' + fnName + ' ');
  }
}

function setInput(text) {
  if (_fnFormActive) closeFnForm();
  var input = document.getElementById('chatInput');
  input.value = text;
  input.focus();
  autoResize(input);
}

