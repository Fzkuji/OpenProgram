// Conversation History — SVG DAG view.
//
// The server ships `conversation_loaded.data.graph`: every message in
// the conversation with id/parent_id/role. We lay it out as a tidy
// tree (leaves get lanes left-to-right, parents center over children)
// and render as a compact minimap. No inline labels — hover shows a
// floating tooltip with the role + preview.
//
// Public:
//   window.renderHistoryGraph(graph, headId)
// Clicks on a node POST /api/chat/checkout, then ask the server to
// reload the conversation so the DAG + main chat refresh together.

(function () {
  var ROW_H = 26;
  var COL_W = 26;
  var NODE_R = 5;
  var HEAD_R = 6.5;
  var PAD = 14;

  var _currentHead = null;
  var _tooltip = null;
  var _lastSignature = null;

  // Fingerprint of the graph so repeated renders of the same snapshot
  // (e.g. streaming updates that don't touch structure) can short-
  // circuit without tearing down the SVG — no flash on same-conv refresh.
  function _signature(graph, headId) {
    if (!graph || !graph.length) return 'empty|' + (headId || '');
    var parts = graph.map(function (m) {
      return m.id + ':' + (m.parent_id || '') + ':' + (m.role || '');
    });
    parts.sort();
    return parts.join(',') + '|' + (headId || '');
  }

  function _roleColor(role, display) {
    if (display === 'runtime') return 'var(--accent-yellow, #d4a017)';
    if (role === 'user') return 'var(--accent-blue, #3b82f6)';
    if (role === 'assistant') return 'var(--accent-green, #22c55e)';
    return 'var(--text-muted, #888)';
  }

  function _buildTree(graph) {
    var byId = Object.create(null);
    graph.forEach(function (m) { byId[m.id] = Object.assign({ children: [] }, m); });
    var roots = [];
    graph.forEach(function (m) {
      var node = byId[m.id];
      if (m.parent_id && byId[m.parent_id]) byId[m.parent_id].children.push(node);
      else roots.push(node);
    });
    function byTs(a, b) { return (a.created_at || 0) - (b.created_at || 0); }
    roots.sort(byTs);
    Object.keys(byId).forEach(function (id) { byId[id].children.sort(byTs); });
    return { roots: roots, byId: byId };
  }

  function _layout(roots) {
    var lane = 0;
    function visit(node, depth) {
      node._depth = depth;
      if (!node.children.length) { node._x = lane++; return; }
      node.children.forEach(function (c) { visit(c, depth + 1); });
      var xs = node.children.map(function (c) { return c._x; });
      node._x = (xs[0] + xs[xs.length - 1]) / 2;
    }
    roots.forEach(function (r) { visit(r, 0); });
    return lane;
  }

  function _headChain(byId, headId) {
    var set = Object.create(null);
    var cur = headId;
    while (cur && byId[cur]) { set[cur] = true; cur = byId[cur].parent_id; }
    return set;
  }

  function _edgePath(x1, y1, x2, y2) {
    var mid = (y1 + y2) / 2;
    return 'M' + x1 + ',' + y1 +
           ' C' + x1 + ',' + mid + ' ' + x2 + ',' + mid + ' ' + x2 + ',' + y2;
  }

  function _svg(tag, attrs) {
    var el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    return el;
  }

  function _ensureTooltip(body) {
    if (_tooltip && _tooltip.parentElement === body) return _tooltip;
    _tooltip = document.createElement('div');
    _tooltip.className = 'history-tooltip';
    body.appendChild(_tooltip);
    return _tooltip;
  }

  function _showTooltip(body, node, x, y) {
    var tip = _ensureTooltip(body);
    var role = node.display === 'runtime'
      ? 'runtime · ' + (node.function || '')
      : (node.role || '?');
    tip.innerHTML = '';
    var r = document.createElement('div');
    r.className = 'history-tooltip-role';
    r.textContent = role;
    tip.appendChild(r);
    var p = document.createElement('div');
    p.textContent = node.preview || '(empty)';
    tip.appendChild(p);
    // Position: left of the node if there's room, else right.
    var bw = body.clientWidth;
    tip.classList.add('visible');
    var tw = tip.offsetWidth;
    var left = x + 14;
    if (left + tw > bw - 6) left = Math.max(6, x - 14 - tw);
    tip.style.left = left + 'px';
    tip.style.top = Math.max(6, y - 10) + 'px';
  }

  function _hideTooltip() {
    if (_tooltip) _tooltip.classList.remove('visible');
  }

  function render(graph, headId) {
    var sig = _signature(graph, headId);
    if (sig === _lastSignature && _currentHead === headId) return;
    _lastSignature = sig;
    _currentHead = headId;

    var panel = document.getElementById('historyPanel');
    if (!panel) return;
    var body = panel.querySelector('.history-body');

    if (!graph || !graph.length) {
      var empty = document.createElement('div');
      empty.className = 'history-empty';
      empty.textContent = 'No messages yet.';
      body.replaceChildren(empty);
      _tooltip = null;
      return;
    }

    var tree = _buildTree(graph);
    var laneCount = _layout(tree.roots);
    var headChain = _headChain(tree.byId, headId);

    var maxDepth = 0;
    Object.keys(tree.byId).forEach(function (id) {
      if (tree.byId[id]._depth > maxDepth) maxDepth = tree.byId[id]._depth;
    });

    var width = PAD * 2 + COL_W * Math.max(laneCount - 1, 0);
    var height = PAD * 2 + ROW_H * maxDepth;

    var svg = _svg('svg', {
      class: 'history-svg',
      viewBox: '0 0 ' + Math.max(width, 40) + ' ' + Math.max(height, 40),
      width: Math.max(width, 40),
      height: Math.max(height, 40),
    });

    var edgeG = _svg('g', { class: 'history-edges' });
    var nodeG = _svg('g', { class: 'history-nodes' });
    svg.appendChild(edgeG);
    svg.appendChild(nodeG);

    function pos(n) {
      return { x: PAD + n._x * COL_W, y: PAD + n._depth * ROW_H };
    }

    Object.keys(tree.byId).forEach(function (id) {
      var node = tree.byId[id];
      var p = pos(node);
      if (node.parent_id && tree.byId[node.parent_id]) {
        var pp = pos(tree.byId[node.parent_id]);
        var onChain = headChain[id] && headChain[node.parent_id];
        edgeG.appendChild(_svg('path', {
          d: _edgePath(pp.x, pp.y, p.x, p.y),
          class: 'history-edge' + (onChain ? ' on-head' : ''),
        }));
      }
      var isHead = id === headId;
      var g = _svg('g', {
        class: 'history-node' + (isHead ? ' is-head' : ''),
        transform: 'translate(' + p.x + ',' + p.y + ')',
        'data-msg-id': id,
      });
      g.appendChild(_svg('circle', {
        r: isHead ? HEAD_R : NODE_R,
        fill: _roleColor(node.role, node.display),
      }));
      // Hover handlers piggy-back on the <g>. Stash the node data on it
      // so the handler doesn't need another lookup.
      g._nodeData = node;
      nodeG.appendChild(g);
    });

    // Atomic swap — no blank frame between old & new graph.
    body.replaceChildren(svg);
    _tooltip = null;

    // Delegated hover — attach once per body element to avoid leaking
    // listeners on repeated renders.
    if (!body._historyHoverWired) {
      body._historyHoverWired = true;
      body.addEventListener('mousemove', function (e) {
        var g = e.target.closest && e.target.closest('.history-node');
        if (!g || !g._nodeData) { _hideTooltip(); return; }
        var rect = body.getBoundingClientRect();
        _showTooltip(body, g._nodeData,
          e.clientX - rect.left + body.scrollLeft,
          e.clientY - rect.top + body.scrollTop);
      });
      body.addEventListener('mouseleave', _hideTooltip);
    }
  }

  async function _checkout(msgId) {
    var convId = window.currentConvId;
    if (!convId || !msgId || msgId === _currentHead) return;
    try {
      var r = await fetch('/api/chat/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conv_id: convId, msg_id: msgId }),
      });
      if (!r.ok) throw new Error(await r.text());
      if (window.ws && window.ws.readyState === WebSocket.OPEN) {
        window.ws.send(JSON.stringify({ action: 'load_conversation', conv_id: convId }));
      }
    } catch (err) {
      console.error('[history-graph] checkout failed:', err);
    }
  }

  document.addEventListener('click', function (e) {
    var g = e.target.closest && e.target.closest('.history-node');
    if (!g) return;
    var id = g.getAttribute('data-msg-id');
    if (id) _checkout(id);
  });

  window.renderHistoryGraph = render;
})();
