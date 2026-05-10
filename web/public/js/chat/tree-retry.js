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
  if (!currentSessionId) {
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
    if (currentSessionId && conversations[currentSessionId]) {
      var _aMsgs = conversations[currentSessionId].messages || [];
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
    session_id: currentSessionId,
    params: params
  }));
}

