// ===== Provider Setup Wizard =====

var _wizardState = null;  // { provider, steps, idx, ctx }

async function openSetupWizard(providerId) {
  try {
    var resp = await fetch('/api/providers/' + encodeURIComponent(providerId) + '/configure');
    if (!resp.ok) {
      alert('No configuration wizard for ' + providerId + ' yet.');
      return;
    }
    var schema = await resp.json();
    _wizardState = { provider: providerId, label: schema.label, description: schema.description || '',
                     steps: schema.steps, idx: 0, ctx: {}, results: [] };
    _renderWizard();
    _runWizardStep();
  } catch(e) {
    alert('Failed to load configuration: ' + e.message);
  }
}

function closeSetupWizard() {
  var o = document.getElementById('setupWizardOverlay');
  if (o) o.remove();
  _wizardState = null;
  // Refresh the providers list so status badges update
  _loadProvidersSettings();
}

function _renderWizard() {
  var existing = document.getElementById('setupWizardOverlay');
  if (existing) existing.remove();

  var s = _wizardState;
  var html = '';
  html += '<div class="code-modal">';
  html += '<div class="code-modal-header">';
  html += '<div class="code-modal-title">Setup: ' + escHtml(s.label) + '</div>';
  html += '<button class="code-modal-close" onclick="closeSetupWizard()">&times;</button>';
  html += '</div>';
  html += '<div class="code-modal-body" style="padding:20px">';
  if (s.description) {
    html += '<div style="color:var(--text-muted);font-size:13px;margin-bottom:16px">' + escHtml(s.description) + '</div>';
  }
  html += '<div id="wizardSteps"></div>';
  html += '</div>';
  html += '</div>';

  var overlay = document.createElement('div');
  overlay.id = 'setupWizardOverlay';
  overlay.className = 'code-modal-overlay active';
  overlay.innerHTML = html;
  document.body.appendChild(overlay);
  _renderWizardSteps();
}

function _renderWizardSteps() {
  var s = _wizardState;
  var container = document.getElementById('wizardSteps');
  if (!container) return;
  var html = '';
  for (var i = 0; i < s.steps.length; i++) {
    var step = s.steps[i];
    var result = s.results[i];
    var icon = '·', color = 'var(--text-muted)';
    if (result) {
      if (result.status === 'ok') { icon = '✓'; color = 'var(--accent-green, #3fb950)'; }
      else if (result.status === 'error') { icon = '✗'; color = 'var(--accent-red, #f85149)'; }
      else if (result.status === 'needs_input') { icon = '?'; color = 'var(--accent-blue, #58a6ff)'; }
    } else if (i === s.idx) {
      icon = '→'; color = 'var(--accent-blue, #58a6ff)';
    }
    html += '<div style="display:flex;align-items:flex-start;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">';
    html += '<div style="color:' + color + ';font-weight:600;min-width:20px">' + icon + '</div>';
    html += '<div style="flex:1">';
    html += '<div style="font-weight:500">' + escHtml(step.label) + '</div>';
    if (result) {
      html += '<div style="color:var(--text-muted);font-size:12px;margin-top:4px">' + escHtml(result.message || '') + '</div>';
      if (result.status === 'error' && result.fix) {
        html += '<div style="margin-top:6px;font-size:12px"><span style="color:var(--text-muted)">Fix: </span><code style="background:var(--bg-tertiary);padding:2px 6px;border-radius:4px">' + escHtml(result.fix) + '</code></div>';
        html += '<div style="margin-top:8px;display:flex;gap:8px">';
        html += '<button class="settings-btn" onclick="_retryWizardStep()">Retry</button>';
        html += '<button class="settings-btn" onclick="closeSetupWizard()">Close</button>';
        html += '</div>';
      }
      if (result.status === 'needs_input') {
        html += _renderWizardInput(i, result);
      }
    }
    html += '</div></div>';
  }
  container.innerHTML = html;
}

function _renderWizardInput(stepIdx, result) {
  var html = '<div style="margin-top:10px">';
  var options = result.options || [];
  if (options.length > 0) {
    html += '<div style="display:flex;flex-direction:column;gap:6px">';
    for (var i = 0; i < options.length; i++) {
      var opt = options[i];
      html += '<label style="display:flex;align-items:center;gap:8px;cursor:pointer;padding:6px 10px;border:1px solid var(--border);border-radius:6px">';
      html += '<input type="radio" name="wizardOpt" value="' + escAttr(opt.value) + '"' + (opt.value === result.default ? ' checked' : '') + '>';
      html += '<span>' + escHtml(opt.value) + '</span>';
      if (opt.desc) html += '<span style="color:var(--text-muted);font-size:12px">— ' + escHtml(opt.desc) + '</span>';
      html += '</label>';
    }
    html += '</div>';
    html += '<button class="settings-btn" style="margin-top:10px" onclick="_submitWizardInput(\'' + escAttr(result.input_key) + '\')">Continue</button>';
  } else {
    html += '<input id="wizardInputField" class="settings-input" type="text" placeholder="' + escAttr(result.default || '') + '">';
    html += '<button class="settings-btn" style="margin-left:8px" onclick="_submitWizardInput(\'' + escAttr(result.input_key) + '\')">Continue</button>';
  }
  html += '</div>';
  return html;
}

function _submitWizardInput(inputKey) {
  var s = _wizardState;
  var picked;
  var radios = document.querySelectorAll('input[name="wizardOpt"]');
  if (radios.length > 0) {
    for (var i = 0; i < radios.length; i++) {
      if (radios[i].checked) { picked = radios[i].value; break; }
    }
  } else {
    var f = document.getElementById('wizardInputField');
    picked = f ? f.value.trim() : '';
  }
  if (!picked) { alert('Please pick a value.'); return; }
  s.ctx[inputKey] = picked;
  // Clear the needs_input result and re-run the same step (it'll now see ctx[inputKey])
  s.results[s.idx] = null;
  _runWizardStep();
}

function _retryWizardStep() {
  _runWizardStep();
}

async function _runWizardStep() {
  var s = _wizardState;
  if (!s || s.idx >= s.steps.length) {
    // All done
    _renderWizardSteps();
    var container = document.getElementById('wizardSteps');
    if (container) {
      container.insertAdjacentHTML('beforeend',
        '<div style="margin-top:16px;padding:12px;background:var(--bg-tertiary);border-radius:6px;color:var(--accent-green,#3fb950)">' +
        'All steps complete. This provider is now configured.</div>' +
        '<div style="margin-top:12px"><button class="settings-btn" onclick="closeSetupWizard()">Done</button></div>'
      );
    }
    return;
  }
  var step = s.steps[s.idx];
  _renderWizardSteps();  // show spinner-ish state for current
  try {
    var resp = await fetch('/api/providers/' + encodeURIComponent(s.provider) +
                           '/configure/step/' + encodeURIComponent(step.id), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(s.ctx),
    });
    var data = await resp.json();
    s.results[s.idx] = data.result;
    s.ctx = data.context || s.ctx;
    _renderWizardSteps();
    if (data.result.status === 'ok') {
      s.idx += 1;
      _runWizardStep();
    }
    // If error or needs_input, stop and wait for user
  } catch(e) {
    s.results[s.idx] = { status: 'error', message: 'Network error: ' + e.message };
    _renderWizardSteps();
  }
}

