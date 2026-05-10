/**
 * workdir.js — working directory chooser for every function form.
 *
 * Workdir is a runtime-level setting, not a function argument. It travels
 * alongside the normal `run <func> key=val ...` command as `work_dir=<path>`;
 * server.py intercepts it before dispatch and routes it into
 * exec_rt.set_workdir().
 *
 * UI: a row with a "Working in a folder" button and a path input. Clicking
 * the button calls /api/pick-folder which pops the OS-native folder
 * chooser (AppleScript). The user can also type/paste a path directly.
 */

function buildWorkdirField() {
  return (
    '<div class="workdir-row">' +
      '<button type="button" class="workdir-btn" onclick="pickWorkdir()" title="Open folder chooser">' +
        '<svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' +
          '<path d="M3 6a2 2 0 0 1 2-2h3l2 2h5a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>' +
        '</svg>' +
        '<span>Working in a folder</span>' +
      '</button>' +
      '<input type="text" class="workdir-input" id="fnField_work_dir" ' +
             'placeholder="/path/to/your/project" spellcheck="false" autocomplete="off" ' +
             'oninput="_workdirOnInput(event)">' +
    '</div>'
  );
}

async function initWorkdirField(fnName) {
  var input = document.getElementById('fnField_work_dir');
  if (!input) return;
  input.dataset.fnName = fnName || '';

  var sessionId = (typeof currentSessionId !== 'undefined') ? currentSessionId : null;
  var url = '/api/workdir/defaults?function_name=' + encodeURIComponent(fnName || '');
  if (sessionId) url += '&session_id=' + encodeURIComponent(sessionId);
  try {
    var r = await fetch(url);
    var data = await r.json();
    window._workdirHome = data.home;
    if (data.last) input.value = data.last;
  } catch (e) {
    // non-fatal — user can still type / pick
  }
}

function _workdirOnInput(e) {
  var input = e.target;
  if (input && input.value.trim()) input.classList.remove('workdir-input-error');
}

async function pickWorkdir() {
  var input = document.getElementById('fnField_work_dir');
  if (!input) return;
  var start = (input.value && input.value.trim()) || window._workdirHome || '';
  try {
    var r = await fetch('/api/pick-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start: start }),
    });
    var data = await r.json();
    if (!r.ok) {
      if (typeof addSystemMessage === 'function') {
        addSystemMessage('Folder picker failed: ' + (data.error || r.status));
      }
      return;
    }
    if (data.path) {
      input.value = data.path;
      input.classList.remove('workdir-input-error');
    }
  } catch (e) {
    if (typeof addSystemMessage === 'function') {
      addSystemMessage('Folder picker failed: ' + e.message);
    }
  }
}
