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
    // While paused, freeze the display and flip the suffix to "(paused)".
    // Don't increment — the user just complained that paused nodes keep
    // ticking, which looks like the run is still consuming wall time.
    if (isPaused) {
      runningDurs.forEach(function(el) {
        var t = el.textContent || '';
        if (t.indexOf('(paused)') === -1) {
          el.textContent = t.replace(/\.{3}\s*$/, '').replace(/\s*$/, '') + ' (paused)';
        }
      });
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

