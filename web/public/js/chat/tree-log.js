// ===== Execution Log =====

function createExecLog() {
  var old = document.getElementById('currentExecLog');
  if (old) { old.id = ''; old.classList.add('collapsed'); }

  var log = document.createElement('div');
  log.id = 'currentExecLog';
  log.className = 'exec-log';
  log.innerHTML =
    '<div class="exec-log-header" onclick="this.parentElement.classList.toggle(\'collapsed\')">' +
      '<div class="spinner-sm"></div>' +
      '<span class="exec-log-title">Executing...</span>' +
      '<span class="exec-log-chevron">&#9660;</span>' +
    '</div>' +
    '<div class="exec-log-body"></div>';
  appendToChat(log);
  execLogStartTime = Date.now();
  scrollToBottom();
}

function addExecLogEntry(eventType, data) {
  var log = document.getElementById('currentExecLog');
  if (!log) return;
  var body = log.querySelector('.exec-log-body');
  if (!body) return;

  var path = data.path || '';
  var depth = path.split('/').length - 1;
  var name = data.name || path.split('/').pop() || '?';
  var indent = '';
  for (var d = 0; d < Math.min(depth, 6); d++) indent += '\u00a0\u00a0';

  if (eventType === 'node_created') {
    var entry = document.createElement('div');
    entry.className = 'exec-log-entry';
    entry.id = 'elog-' + path.replace(/[^a-zA-Z0-9_]/g, '-');

    var paramsStr = '';
    if (data.params) {
      var p = data.params;
      var keys = Object.keys(p).filter(function(k) { return k !== 'runtime' && k !== 'callback'; });
      if (keys.length > 0) {
        paramsStr = '(' + keys.map(function(k) {
          var v = String(p[k] || '');
          if (v.length > 30) v = v.slice(0, 27) + '...';
          return k + '=' + v;
        }).join(', ') + ')';
      }
    }

    entry.innerHTML =
      '<span class="exec-log-indent">' + indent + '</span>' +
      '<span class="exec-log-icon running">&#9654;</span>' +
      '<span class="exec-log-name" onclick="viewSource(\'' + escAttr(name) + '\')" title="View source">' + escHtml(name) + '</span>' +
      (paramsStr ? '<span class="exec-log-params">' + escHtml(paramsStr) + '</span>' : '') +
      '<span class="exec-log-time"></span>';
    body.appendChild(entry);
    body.scrollTop = body.scrollHeight;
    scrollToBottom();
  }

  if (eventType === 'node_completed') {
    var entryId = 'elog-' + path.replace(/[^a-zA-Z0-9_]/g, '-');
    var entryEl = document.getElementById(entryId);
    if (entryEl) {
      var icon = entryEl.querySelector('.exec-log-icon');
      var timeEl = entryEl.querySelector('.exec-log-time');
      var hasError = data.error || data.status === 'error';
      if (icon) {
        icon.className = 'exec-log-icon ' + (hasError ? 'error' : 'done');
        icon.innerHTML = hasError ? '&#10007;' : '&#10003;';
      }
      if (timeEl && data.duration_ms) {
        var ms = data.duration_ms;
        timeEl.textContent = ms < 1000 ? ms + 'ms' : (ms / 1000).toFixed(1) + 's';
      }
      if (data.output && !hasError) {
        var outStr = String(data.output);
        if (outStr.length > 80) outStr = outStr.slice(0, 77) + '...';
        if (outStr && outStr !== 'None' && outStr !== 'null') {
          var outDiv = document.createElement('div');
          outDiv.className = 'exec-log-entry';
          outDiv.innerHTML = '<span class="exec-log-indent">' + indent + '\u00a0\u00a0</span>' +
            '<span class="exec-log-output">\u2192 ' + escHtml(outStr) + '</span>';
          entryEl.after(outDiv);
        }
      }
    }
  }
}

function finalizeExecLog() {
  var log = document.getElementById('currentExecLog');
  if (!log) return;
  var spinner = log.querySelector('.spinner-sm');
  if (spinner) {
    spinner.outerHTML = '<span style="color:var(--accent-green);font-size:12px">&#10003;</span>';
  }
  var title = log.querySelector('.exec-log-title');
  if (title) {
    var elapsed = Date.now() - execLogStartTime;
    var timeStr = elapsed < 1000 ? elapsed + 'ms' : (elapsed / 1000).toFixed(1) + 's';
    title.textContent = 'Completed in ' + timeStr;
  }
  setTimeout(function() {
    if (log.id === 'currentExecLog') {
      log.id = '';
      log.classList.add('collapsed');
    }
  }, 2000);
}

// ===== Context Card =====

function renderContextCard(tree, treeId) {
  var id = treeId || 'ctx_' + Date.now();
  expandedNodes.add(tree.path);
  return '<div class="context-card">' +
    '<div class="context-card-header" onclick="toggleContextCard(\'' + id + '\')">' +
      '<span class="context-card-title">' +
        '<span style="color:var(--accent-cyan)">&#9670;</span> Execution Tree: ' + escHtml(tree.name) +
      '</span>' +
      '<span class="context-card-toggle" id="toggle_' + id + '">&#9654;</span>' +
    '</div>' +
    '<div class="context-card-body" id="body_' + id + '">' +
      renderTreeNode(tree) +
    '</div>' +
  '</div>';
}

function toggleContextCard(id) {
  var body = document.getElementById('body_' + id);
  var toggle = document.getElementById('toggle_' + id);
  if (body && toggle) {
    var expanded = body.classList.toggle('expanded');
    toggle.classList.toggle('expanded', expanded);
  }
}

// ===== Attempt Navigation =====

function renderAttemptNav(funcName, currentIdx, total) {
  var prevDisabled = currentIdx <= 0 ? ' disabled' : '';
  var nextDisabled = currentIdx >= total - 1 ? ' disabled' : '';
  return '<div class="attempt-nav">' +
    '<button class="attempt-nav-btn"' + prevDisabled + ' onclick="switchAttempt(\'' + escAttr(funcName) + '\', -1)" title="Previous attempt">&#9664;</button>' +
    '<span class="attempt-nav-label">' + (currentIdx + 1) + '/' + total + '</span>' +
    '<button class="attempt-nav-btn"' + nextDisabled + ' onclick="switchAttempt(\'' + escAttr(funcName) + '\', 1)" title="Next attempt">&#9654;</button>' +
  '</div>';
}

function switchAttempt(funcName, direction) {
  if (!currentConvId || !conversations[currentConvId]) return;
  var msgs = conversations[currentConvId].messages || [];
  var msg = null;
  for (var i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === 'assistant' && msgs[i].function === funcName && msgs[i].attempts) {
      msg = msgs[i];
      break;
    }
  }
  if (!msg || !msg.attempts || msg.attempts.length <= 1) return;

  var newIdx = (msg.current_attempt || 0) + direction;
  if (newIdx < 0 || newIdx >= msg.attempts.length) return;

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      action: 'switch_attempt',
      conv_id: currentConvId,
      function: funcName,
      attempt_index: newIdx
    }));
  }
}
