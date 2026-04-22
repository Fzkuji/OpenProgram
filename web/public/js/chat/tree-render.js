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

