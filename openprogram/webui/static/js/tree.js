// ===== Tree Data Management & Rendering =====

function renderLiveTree() {
  var old = document.getElementById('liveExecTree');
  if (old) old.remove();
}

function startElapsedTimer() {
  if (_elapsedTimer) return;
  _elapsedTimer = setInterval(function() {
    var runningDurs = document.querySelectorAll('.node-duration[data-running]');
    if (runningDurs.length === 0) {
      clearInterval(_elapsedTimer);
      _elapsedTimer = null;
      return;
    }
    runningDurs.forEach(function(el) {
      var startTime = parseFloat(el.getAttribute('data-start'));
      if (startTime > 0) {
        var elapsed = Math.round(Date.now() / 1000 - startTime);
        el.textContent = elapsed + 's...';
      }
    });
  }, 1000);
}

function refreshInlineTrees() {
  var treeBodies = document.querySelectorAll('.inline-tree-body');
  treeBodies.forEach(function(body) {
    var path = body.getAttribute('data-root-path');
    if (path && _nodeCache[path]) {
      body.innerHTML = renderTreeNode(_nodeCache[path]);
    }
  });
  window._lastTreeJson = null;
}

function _treeHasRunning(node) {
  if (!node) return false;
  // A finished end_time means the node is no longer running even if status
  // hasn't been updated yet (race on cancellation).
  var ended = (node.duration_ms && node.duration_ms > 0) ||
              (node.end_time && node.end_time > 0);
  if (node.status === 'running' && !ended) return true;
  if (node.children) {
    for (var i = 0; i < node.children.length; i++) {
      if (_treeHasRunning(node.children[i])) return true;
    }
  }
  return false;
}

function toggleLiveTree() {
  _liveTreeCollapsed = !_liveTreeCollapsed;
  var body = document.getElementById('body_liveExecTreeCard');
  var toggle = document.getElementById('toggle_liveExecTreeCard');
  if (body) body.classList.toggle('expanded', !_liveTreeCollapsed);
  if (toggle) toggle.classList.toggle('expanded', !_liveTreeCollapsed);
}

function updateTreeData(nodeData) {
  var path = nodeData.path || '';
  var parts = path.split('/');
  if (parts.length === 1) {
    var idx = trees.findIndex(function(t) { return t.path === path || t.name === nodeData.name; });
    if (idx >= 0) {
      trees[idx] = mergeNode(trees[idx], nodeData);
    } else {
      trees.push(nodeData);
      expandedNodes.add(path);
    }
    return;
  }
  for (var i = 0; i < trees.length; i++) {
    if (updateNodeInTree(trees[i], nodeData)) return;
  }
  var rootName = parts[0];
  var rootIdx = trees.findIndex(function(t) { return t.path === rootName || t.name === rootName; });
  if (rootIdx >= 0) {
    if (!trees[rootIdx].children) trees[rootIdx].children = [];
    trees[rootIdx].children.push(nodeData);
    expandedNodes.add(trees[rootIdx].path);
  } else {
    trees.push({
      name: rootName, path: rootName, status: 'running',
      children: [nodeData], duration_ms: 0
    });
    expandedNodes.add(rootName);
  }
}

function updateNodeInTree(tree, nodeData) {
  if (tree.path === nodeData.path) {
    Object.assign(tree, nodeData, { children: mergeChildren(tree.children, nodeData.children) });
    return true;
  }
  for (var i = 0; i < (tree.children || []).length; i++) {
    if (updateNodeInTree(tree.children[i], nodeData)) return true;
  }
  if (nodeData.path && nodeData.path.startsWith(tree.path + '/')) {
    var depth = nodeData.path.split('/').length - tree.path.split('/').length;
    if (depth === 1) {
      var existIdx = (tree.children || []).findIndex(function(c) { return c.path === nodeData.path; });
      if (existIdx >= 0) {
        tree.children[existIdx] = mergeNode(tree.children[existIdx], nodeData);
      } else {
        if (!tree.children) tree.children = [];
        tree.children.push(nodeData);
        expandedNodes.add(tree.path);
      }
      return true;
    }
  }
  return false;
}

function mergeNode(existing, incoming) {
  return Object.assign({}, existing, incoming, { children: mergeChildren(existing.children, incoming.children) });
}

function mergeChildren(existing, incoming) {
  if (!incoming || incoming.length === 0) return existing || [];
  if (!existing || existing.length === 0) return incoming;
  var merged = existing.slice();
  for (var j = 0; j < incoming.length; j++) {
    var inc = incoming[j];
    var idx = merged.findIndex(function(m) { return m.path === inc.path; });
    if (idx >= 0) merged[idx] = mergeNode(merged[idx], inc);
    else merged.push(inc);
  }
  return merged;
}

// ===== Inline Tree Rendering =====

function renderInlineTree(tree, treeId) {
  if (!tree) return '';
  var id = treeId || 'itree_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
  function _expandAll(n) {
    if (n.path) expandedNodes.add(n.path);
    if (n.children) n.children.forEach(_expandAll);
  }
  _expandAll(tree);
  var hasRunning = _treeHasRunning(tree);
  var statusIcon = hasRunning
    ? '<span class="pulse" style="color:var(--accent-blue)">&#9679;</span> '
    : '<span style="color:var(--accent-cyan)">&#9670;</span> ';
  var rootPath = tree.path || '';
  return '<div class="inline-tree">' +
    '<div class="inline-tree-header" onclick="toggleInlineTree(\'' + id + '\')">' +
      '<span>' + statusIcon + 'Execution Tree</span>' +
      '<span class="inline-tree-actions">' +
        '<button class="inline-tree-copy" onclick="event.stopPropagation();copyInlineTree(event, \'' + escAttr(rootPath) + '\')" title="Copy tree as JSON">Copy JSON</button>' +
        '<span class="inline-tree-toggle" id="itoggle_' + id + '">&#9654;</span>' +
      '</span>' +
    '</div>' +
    '<div class="inline-tree-body" id="ibody_' + id + '" data-root-path="' + escAttr(rootPath) + '">' +
      renderTreeNode(tree) +
    '</div>' +
  '</div>';
}

function copyInlineTree(ev, rootPath) {
  var root = _nodeCache[rootPath];
  if (!root) return;
  function clean(n) {
    var c = {};
    for (var k in n) {
      if (k === 'children') continue;
      if (k === 'params' && n.params && typeof n.params === 'object') {
        var p = {};
        for (var pk in n.params) {
          if (pk !== 'runtime' && pk !== 'callback') p[pk] = n.params[pk];
        }
        c.params = p;
      } else {
        c[k] = n[k];
      }
    }
    if (n.children && n.children.length) {
      c.children = n.children.map(clean);
    }
    return c;
  }
  var json = JSON.stringify(clean(root), null, 2);
  var btn = ev && ev.currentTarget;
  var done = function() {
    if (!btn) return;
    var prev = btn.textContent;
    btn.textContent = 'Copied';
    btn.classList.add('copied');
    setTimeout(function() { btn.textContent = prev; btn.classList.remove('copied'); }, 1200);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(json).then(done, function() { _treeFallbackCopy(json); done(); });
  } else {
    _treeFallbackCopy(json);
    done();
  }
}

function _treeFallbackCopy(text) {
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.top = '-1000px';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } catch(e) {}
  document.body.removeChild(ta);
}

function toggleInlineTree(id) {
  var body = document.getElementById('ibody_' + id);
  var toggle = document.getElementById('itoggle_' + id);
  if (body && toggle) {
    body.classList.toggle('collapsed');
    toggle.classList.toggle('collapsed');
  }
}

function toggleRuntimeBlock(headerEl) {
  var block = headerEl.closest('.runtime-block');
  if (block) block.classList.toggle('collapsed');
}

function renderTreeNode(node) {
  _nodeCache[node.path] = node;
  var hasChildren = node.children && node.children.length > 0;
  var isExpanded = expandedNodes.has(node.path);
  var isSelected = node.path === selectedPath;

  // Treat any node with a finite end_time / duration as done, even if status
  // slipped through as "running" (e.g. cancellation racing with emit events).
  var hasFinished = (node.duration_ms && node.duration_ms > 0) ||
                    (node.end_time && node.end_time > 0);
  var effectiveStatus = (node.status === 'running' && hasFinished) ? 'error' : node.status;
  var isCancelled = effectiveStatus === 'error' &&
                    typeof node.error === 'string' &&
                    /cancel/i.test(node.error);

  var displayStatus = (isPaused && effectiveStatus === 'running') ? 'paused' : effectiveStatus;

  var icon = displayStatus === 'success'
    ? '<span style="color:var(--accent-green)">&#10003;</span>'
    : isCancelled
    ? '<span style="color:var(--text-muted)" title="Cancelled">&#9673;</span>'
    : displayStatus === 'error'
    ? '<span style="color:var(--accent-red)">&#10007;</span>'
    : displayStatus === 'paused'
    ? '<span style="color:var(--accent-yellow)">&#10074;&#10074;</span>'
    : '<span class="pulse" style="color:var(--accent-blue)">&#9679;</span>';

  var dur = '';
  if (node.duration_ms > 0) {
    dur = node.duration_ms >= 1000 ? (node.duration_ms / 1000).toFixed(1) + 's' : Math.round(node.duration_ms) + 'ms';
  } else if (displayStatus === 'running' && node.start_time > 0) {
    var elapsed = Math.round(Date.now() / 1000 - node.start_time);
    dur = elapsed + 's...';
  } else if (displayStatus === 'paused' && node.start_time > 0) {
    var elapsed = Math.round(Date.now() / 1000 - node.start_time);
    dur = elapsed + 's (paused)';
  }

  var isExec = node.node_type === 'exec';
  var output = '';
  var preview = '';
  if (isExec) {
    var execIn = (node.params && node.params._content) || '';
    var execOut = node.raw_reply || (typeof node.output === 'string' ? node.output : '');
    var inPart = execIn ? '\u2192 ' + truncate(execIn, 50) : '';
    var outPart = execOut ? ' \u2190 ' + truncate(execOut, 50) : '';
    preview = (inPart + outPart).trim();
  } else if (node.output != null) {
    output = typeof node.output === 'string'
      ? truncate(node.output, 80)
      : truncate(JSON.stringify(node.output), 80);
  }

  var toggleClass = hasChildren ? (isExpanded ? 'expanded' : '') : 'leaf';
  var childrenClass = isExpanded ? '' : 'collapsed';

  var canRetry = !isExec && node.name !== 'chat_session' && node.status !== 'running';
  var filteredParams = {};
  if (node.params) {
    for (var k in node.params) { if (k !== 'runtime' && k !== 'callback') filteredParams[k] = node.params[k]; }
  }

  var nameCell = isExec
    ? '<span class="llm-badge" title="LLM call">LLM</span>'
    : '<span class="node-name" onclick="event.stopPropagation();viewSource(\'' + escAttr(node.name) + '\')" title="View source" style="cursor:pointer">' + escHtml(node.name) + '</span>';

  var html = '<div class="tree-node">' +
    '<div class="node-row' + (isSelected ? ' selected' : '') + (isExec ? ' exec-row' : '') + '" onclick="selectTreeNode(event, \'' + escAttr(node.path) + '\')">' +
      '<span class="node-toggle ' + toggleClass + '" onclick="toggleExpand(event, \'' + escAttr(node.path) + '\')">&#9654;</span>' +
      '<span class="node-icon">' + icon + '</span>' +
      nameCell +
      (isExec ? '' : '<span class="node-status ' + displayStatus + (isCancelled ? ' cancelled' : '') + '">' + (isCancelled ? 'cancelled' : displayStatus) + '</span>') +
      (dur ? '<span class="node-duration"' + ((displayStatus === 'running' || displayStatus === 'paused') && node.start_time > 0 && !hasFinished ? ' data-running="1" data-start="' + node.start_time + '"' : '') + '>' + dur + '</span>' : '') +
      (preview ? '<span class="node-output-preview exec-preview">' + escHtml(preview) + '</span>' : '') +
      (output ? '<span class="node-output-preview">' + escHtml(output) + '</span>' : '') +
      (canRetry ? '<span class="retry-icon" onclick="event.stopPropagation();toggleRetryPanel(\'' + escAttr(node.path) + '\')" title="Modify">modify</span>' : '') +
    '</div>';

  if (canRetry) {
    var panelId = 'retryPanel_' + node.path.replace(/[^a-zA-Z0-9]/g, '_');
    var paramKeys = Object.keys(filteredParams);
    html += '<div class="retry-panel" id="' + panelId + '" style="display:none">';
    html += '<div style="margin-bottom:6px;color:var(--text-secondary);font-size:11px">Modify <b>' + escHtml(node.name) + '</b> with:</div>';
    if (paramKeys.length === 0) {
      html += '<div style="color:var(--text-muted);font-size:11px;margin-bottom:6px">No editable parameters</div>';
    } else {
      html += _buildRetryFields(filteredParams, '', node.path);
    }
    html += '<div class="retry-panel-actions">' +
      '<button class="retry-exec-btn" onclick="executeRetry(\'' + escAttr(node.path) + '\')">&#9654; Execute</button>' +
      '<button class="retry-cancel-btn" onclick="toggleRetryPanel(\'' + escAttr(node.path) + '\')">Cancel</button>' +
    '</div></div>';
  }

  if (hasChildren) {
    html += '<div class="node-children ' + childrenClass + '">';
    for (var ci = 0; ci < node.children.length; ci++) {
      html += renderTreeNode(node.children[ci]);
    }
    html += '</div>';
  }

  html += '</div>';
  return html;
}

function selectTreeNode(event, pathOrData) {
  event.stopPropagation();
  var node;
  if (typeof pathOrData === 'string') {
    node = _nodeCache[pathOrData] || _findNodeByPath(pathOrData);
  } else {
    node = pathOrData;
  }
  if (node) showDetail(node);
}

function toggleExpand(event, path) {
  event.stopPropagation();
  if (expandedNodes.has(path)) expandedNodes.delete(path);
  else expandedNodes.add(path);
  var row = event.target.closest('.node-row');
  if (row) {
    var treeNode = row.closest('.tree-node');
    var children = treeNode ? treeNode.querySelector(':scope > .node-children') : null;
    if (children) children.classList.toggle('collapsed');
    var toggle = row.querySelector('.node-toggle');
    if (toggle) toggle.classList.toggle('expanded');
  }
}

function _findNodeByPath(path) {
  for (var i = 0; i < trees.length; i++) {
    var found = _findInTree(trees[i], path);
    if (found) return found;
  }
  return null;
}

function _findInTree(node, path) {
  if (node.path === path) return node;
  if (node.children) {
    for (var i = 0; i < node.children.length; i++) {
      var found = _findInTree(node.children[i], path);
      if (found) return found;
    }
  }
  return null;
}

// ===== Retry Panel =====

function _buildRetryFields(params, prefix, nodePath) {
  var html = '';
  var keys = Object.keys(params);
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    var v = params[k];
    var fullKey = prefix ? prefix + '.' + k : k;
    if (k === 'runtime' || k === 'callback') continue;
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      html += '<div class="retry-field">' +
        '<label class="retry-field-label">' + escHtml(k) + '</label>' +
        '<div class="retry-field-group">' + _buildRetryFields(v, fullKey, nodePath) + '</div>' +
      '</div>';
    } else {
      var vs = typeof v === 'string' ? v : JSON.stringify(v);
      var isLong = vs.length > 60 || vs.indexOf('\n') >= 0;
      html += '<div class="retry-field">' +
        '<label class="retry-field-label">' + escHtml(k) + '</label>';
      if (isLong) {
        html += '<textarea class="retry-field-input" data-param="' + escAttr(fullKey) + '" data-path="' + escAttr(nodePath) + '">' + escHtml(vs) + '</textarea>';
      } else {
        html += '<input class="retry-field-input" data-param="' + escAttr(fullKey) + '" data-path="' + escAttr(nodePath) + '" value="' + escAttr(vs) + '" />';
      }
      html += '</div>';
    }
  }
  return html;
}

function toggleRetryPanel(path) {
  var id = 'retryPanel_' + path.replace(/[^a-zA-Z0-9]/g, '_');
  var panel = document.getElementById(id);
  if (panel) panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
}

function executeRetry(path, paramsOverride) {
  var node = _nodeCache[path] || _findNodeByPath(path);
  if (!node) {
    addSystemMessage('Retry failed: node not found in tree. Try refreshing.');
    return;
  }
  if (node.status === 'running') return;

  var params = paramsOverride || null;
  if (!params) {
    params = {};
    var fields = document.querySelectorAll('.retry-field-input[data-path="' + path + '"]');
    for (var i = 0; i < fields.length; i++) {
      var key = fields[i].getAttribute('data-param');
      var val = fields[i].value;
      var parsed;
      try { parsed = JSON.parse(val); } catch(e) { parsed = val; }
      var parts = key.split('.');
      var obj = params;
      for (var j = 0; j < parts.length - 1; j++) {
        if (!obj[parts[j]] || typeof obj[parts[j]] !== 'object') obj[parts[j]] = {};
        obj = obj[parts[j]];
      }
      obj[parts[parts.length - 1]] = parsed;
    }
  }

  toggleRetryPanel(path);

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    addSystemMessage('Retry failed: not connected to server. Try refreshing.');
    return;
  }
  if (!currentConvId) {
    addSystemMessage('Retry failed: no active conversation. Send a message first.');
    return;
  }

  var retryBtn = document.querySelector('.retry-field-input[data-path="' + path + '"]');
  var runtimeBlock = retryBtn ? retryBtn.closest('.runtime-block') : null;
  if (!runtimeBlock) {
    var rootFunc = path.split('/')[0];
    runtimeBlock = document.querySelector('.runtime-block[data-function="' + rootFunc + '"]');
  }
  if (!runtimeBlock) {
    var allBlocks = document.querySelectorAll('.runtime-block');
    if (allBlocks.length > 0) runtimeBlock = allBlocks[allBlocks.length - 1];
  }
  if (runtimeBlock) {
    var oldPending = document.getElementById('runtime_pending');
    if (oldPending && oldPending !== runtimeBlock) oldPending.id = '';
    runtimeBlock.id = 'runtime_pending';
    runtimeBlock.className = 'runtime-block runtime-block-pending';
    var existingHeader = runtimeBlock.querySelector('.runtime-block-header');
    var headerHtml = existingHeader ? existingHeader.outerHTML : '';

    // Preserve attempt nav during loading
    var _attemptFooter = '';
    var _rootFunc = path.split('/')[0];
    var _prevTotal = 0;
    if (currentConvId && conversations[currentConvId]) {
      var _aMsgs = conversations[currentConvId].messages || [];
      for (var _ai = _aMsgs.length - 1; _ai >= 0; _ai--) {
        if (_aMsgs[_ai].role === 'assistant' && _aMsgs[_ai].function === _rootFunc && _aMsgs[_ai].attempts) {
          _prevTotal = _aMsgs[_ai].attempts.length;
          break;
        }
      }
    }
    if (_prevTotal > 0) {
      var _newTotal = _prevTotal + 1;
      _attemptFooter = '<div class="runtime-block-footer">' +
        '<div class="runtime-footer-left"></div>' +
        '<div class="runtime-footer-center">' +
          '<div class="attempt-nav">' +
            '<button class="attempt-nav-btn" disabled title="Previous attempt">&#9664;</button>' +
            '<span class="attempt-nav-label">' + _newTotal + '/' + _newTotal + '</span>' +
            '<button class="attempt-nav-btn" disabled title="Next attempt">&#9654;</button>' +
          '</div>' +
        '</div>' +
        '<div class="runtime-footer-right"></div>' +
      '</div>';
    }

    runtimeBlock.innerHTML = headerHtml +
      '<div class="runtime-block-body"><div class="runtime-block-content">' +
        '<div class="typing-indicator"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>' +
      '</div></div>' + _attemptFooter;
  }

  setRunning(true);

  ws.send(JSON.stringify({
    action: 'retry_node',
    node_path: path,
    conv_id: currentConvId,
    params: params
  }));
}

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
