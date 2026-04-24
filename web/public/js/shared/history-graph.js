// Conversation History — PyCharm-style DAG view.
//
// Layout model (mirrors IntelliJ/PyCharm's Git log panel):
//   * Each LEAF of the DAG owns one lane (column). Walking up from the
//     leaf, every ancestor not yet claimed by an earlier leaf is painted
//     in that lane's colour. The result: each branch reads as a single
//     continuous coloured ribbon from its tip up to the fork point.
//   * Colour encodes BRANCH (lane). Shape encodes ROLE:
//       user      → circle
//       assistant → triangle
//       runtime   → diamond
//     Using both channels means we never rely on colour alone for role,
//     which would collide with the branch palette.
//   * HEAD is highlighted with a ring + subtle drop shadow regardless of
//     which lane it sits on.
//   * Click any node on a branch → checkout that branch's TIP. "Clicking
//     on a branch" means "switch to that branch" in the git sense, not
//     "rewind to this exact commit".
//
// Public: window.renderHistoryGraph(graph, headId)

(function () {
  var ROW_H = 28;
  var COL_W = 22;
  var NODE_R = 5;
  var HEAD_HALO = 8;
  var PAD_X = 18;
  var PAD_Y = 16;

  // Branch palette — distinct hues, first one is the HEAD branch's
  // colour so the active line reads as the canonical blue.
  var LANE_COLORS = [
    '#3b82f6', // blue
    '#22c55e', // green
    '#f59e0b', // amber
    '#a855f7', // purple
    '#ef4444', // red
    '#06b6d4', // cyan
    '#ec4899', // pink
    '#84cc16', // lime
    '#14b8a6', // teal
    '#f97316', // orange
  ];

  var _currentHead = null;
  var _currentViewId = null;          // msgId whose node gets the white outline
  var _headAncestorSet = Object.create(null); // set of ids on the HEAD branch
  var _tooltip = null;
  var _lastSignature = null;
  var _leafOfNode = Object.create(null); // msgId -> leaf msgId (branch tip)

  function _laneColor(i) {
    return LANE_COLORS[i % LANE_COLORS.length];
  }

  function _signature(graph, headId) {
    if (!graph || !graph.length) return 'empty|' + (headId || '');
    var parts = graph.map(function (m) {
      return m.id + ':' + (m.parent_id || '') + ':' + (m.role || '') + ':' + (m.display || '');
    });
    parts.sort();
    return parts.join(',') + '|' + (headId || '');
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

  // Depth assignment (row). Parent is always above its children.
  function _assignDepth(roots) {
    var maxDepth = 0;
    function walk(n, d) {
      n._depth = d;
      if (d > maxDepth) maxDepth = d;
      n.children.forEach(function (c) { walk(c, d + 1); });
    }
    roots.forEach(function (r) { walk(r, 0); });
    return maxDepth;
  }

  // Walk from headId up to root, collecting ids. Returns [] if head unknown.
  function _headAncestors(byId, headId) {
    var out = [];
    var cur = headId;
    while (cur && byId[cur]) { out.push(cur); cur = byId[cur].parent_id; }
    return out;
  }

  // Find the leaf reachable from `start` by always taking the latest child.
  // Used when head itself isn't a leaf (unusual but possible after rewind).
  function _tipFrom(byId, start) {
    var cur = byId[start];
    while (cur && cur.children.length) {
      cur = cur.children[cur.children.length - 1];
    }
    return cur ? cur.id : start;
  }

  // Lane assignment — PyCharm-style.
  //   1. Order leaves by a STABLE key (creation time, ascending) so a
  //      branch keeps its lane regardless of which branch is currently
  //      HEAD. Without this, clicking a different branch re-assigns
  //      every branch to a new lane and the user loses their spatial
  //      bearings — "I was just looking at the left branch, where did
  //      it go?".
  //   2. For each leaf, walk parent chain and claim every un-claimed
  //      ancestor into the leaf's lane. Each fork inherits its own
  //      lane from branch-point downward.
  //
  //   HEAD is identified at render time (thicker stroke on the node,
  //   thicker edges along the HEAD spine), not by reserving lane 0.
  function _assignLanes(byId, roots, headId) {
    var leaves = [];
    Object.keys(byId).forEach(function (id) {
      if (!byId[id].children.length) leaves.push(byId[id]);
    });

    // Stable sort: earliest leaf first → lane 0. Ties on created_at
    // break on id so the ordering is deterministic across sessions.
    leaves.sort(function (a, b) {
      var dt = (a.created_at || 0) - (b.created_at || 0);
      if (dt !== 0) return dt;
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });

    // Claim ancestors.
    var leafOfNode = Object.create(null);
    leaves.forEach(function (leaf, laneIdx) {
      leaf._lane = laneIdx;
      var cur = leaf;
      while (cur) {
        if (cur._lane === undefined) cur._lane = laneIdx;
        leafOfNode[cur.id] = leaf.id;
        var parent = cur.parent_id ? byId[cur.parent_id] : null;
        // Stop climbing as soon as we reach a node already owned by an
        // earlier leaf — that ancestor (and everything above it) belongs
        // to the earlier branch.
        if (parent && parent._lane !== undefined && parent._lane !== laneIdx) break;
        cur = parent;
      }
    });

    // Orphan fallback (shouldn't happen, but be safe).
    Object.keys(byId).forEach(function (id) {
      if (byId[id]._lane === undefined) byId[id]._lane = 0;
      if (!(id in leafOfNode)) leafOfNode[id] = id;
    });

    return { leaves: leaves, laneCount: leaves.length || 1, leafOfNode: leafOfNode };
  }

  function _svg(tag, attrs) {
    var el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    return el;
  }

  // Edge geometry. When child & parent share a lane, draw a dead-straight
  // vertical segment — that's what makes the branch read as a continuous
  // ribbon. Cross-lane edges (forks) curve smoothly from parent's lane
  // into the child's lane and are painted in the CHILD's colour, so the
  // new branch's colour takes over exactly at the fork.
  function _edgePath(x1, y1, x2, y2) {
    if (x1 === x2) return 'M' + x1 + ',' + y1 + ' L' + x2 + ',' + y2;
    var my = (y1 + y2) / 2;
    return 'M' + x1 + ',' + y1 +
           ' C' + x1 + ',' + my + ' ' + x2 + ',' + my + ' ' + x2 + ',' + y2;
  }

  function _shapeFor(node) {
    var role = node.role;
    var display = node.display;
    if (display === 'runtime') return 'square';
    if (role === 'assistant') return 'triangle';
    if (role === 'user') return 'circle';
    return 'circle';
  }

  function _appendShape(parent, shape, color, isCurrent) {
    // The "current view" node (where the chat is scrolled to) gets a
    // thick outline + visible size bump. Pure white on light lane
    // colours (amber, lime, cyan) was hard to pick out; a thicker
    // stroke and a clearly larger shape reads much more obviously.
    var r = isCurrent ? NODE_R + 1.8 : NODE_R;
    var common = {
      fill: color,
      stroke: isCurrent ? '#ffffff' : 'rgba(0,0,0,0.35)',
      'stroke-width': isCurrent ? 3.2 : 1,
    };
    if (shape === 'circle') {
      parent.appendChild(_svg('circle', Object.assign({ r: r }, common)));
    } else if (shape === 'triangle') {
      // Equilateral-ish triangle pointing up. Use slightly larger bbox
      // so visual weight matches the circle.
      var t = r + 1.2;
      var pts = '0,' + (-t) + ' ' + t + ',' + (t * 0.85) + ' ' + (-t) + ',' + (t * 0.85);
      parent.appendChild(_svg('polygon', Object.assign({ points: pts }, common)));
    } else if (shape === 'square') {
      var s = r - 0.2;
      parent.appendChild(_svg('rect', Object.assign({
        x: -s, y: -s, width: s * 2, height: s * 2, rx: 0.8, ry: 0.8,
      }, common)));
    }
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
      _leafOfNode = Object.create(null);
      return;
    }

    var tree = _buildTree(graph);
    var maxDepth = _assignDepth(tree.roots);
    var lanes = _assignLanes(tree.byId, tree.roots, headId);
    _leafOfNode = lanes.leafOfNode;

    var headAncestors = Object.create(null);
    _headAncestors(tree.byId, headId).forEach(function (id) { headAncestors[id] = true; });
    _headAncestorSet = headAncestors;

    // Default the "current view" marker to HEAD when no prior selection
    // exists, or when the prior selection is no longer in the graph.
    if (!_currentViewId || !tree.byId[_currentViewId]) _currentViewId = headId;

    var width = PAD_X * 2 + COL_W * Math.max(lanes.laneCount - 1, 0);
    var height = PAD_Y * 2 + ROW_H * maxDepth;

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
      return { x: PAD_X + n._lane * COL_W, y: PAD_Y + n._depth * ROW_H };
    }

    // Edges first (so nodes overlap them cleanly).
    Object.keys(tree.byId).forEach(function (id) {
      var node = tree.byId[id];
      if (!node.parent_id || !tree.byId[node.parent_id]) return;
      var parent = tree.byId[node.parent_id];
      var p = pos(parent), c = pos(node);
      // Edge colour = CHILD's lane colour. This is what makes fork edges
      // (cross-lane) "hand off" into the new branch's colour at the top.
      var color = _laneColor(node._lane);
      var onHead = headAncestors[id] && headAncestors[node.parent_id];
      edgeG.appendChild(_svg('path', {
        d: _edgePath(p.x, p.y, c.x, c.y),
        stroke: color,
        'stroke-width': onHead ? 2 : 1.6,
        fill: 'none',
        'stroke-linecap': 'round',
        opacity: onHead ? 1 : 0.85,
        class: 'history-edge' + (onHead ? ' on-head' : ''),
      }));
    });

    // Nodes.
    Object.keys(tree.byId).forEach(function (id) {
      var node = tree.byId[id];
      var p = pos(node);
      var isHead = id === headId;
      var isCurrent = id === _currentViewId;
      var color = _laneColor(node._lane);
      var g = _svg('g', {
        class: 'history-node'
          + (isHead ? ' is-head' : '')
          + (isCurrent ? ' is-current' : ''),
        transform: 'translate(' + p.x + ',' + p.y + ')',
        'data-msg-id': id,
      });
      _appendShape(g, _shapeFor(node), color, isCurrent);
      g._nodeData = node;
      nodeG.appendChild(g);
    });

    body.replaceChildren(svg);
    _tooltip = null;

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

  // Update the white-outline cursor without re-rendering the whole SVG.
  // Cheap DOM tweak so this can run on every chat-scroll event.
  function _setCurrentView(msgId) {
    if (!msgId || msgId === _currentViewId) return;
    _currentViewId = msgId;
    var panel = document.getElementById('historyPanel');
    if (!panel) return;
    var body = panel.querySelector('.history-body');
    if (!body) return;
    body.querySelectorAll('.history-node').forEach(function (g) {
      var id = g.getAttribute('data-msg-id');
      var active = id === msgId;
      g.classList.toggle('is-current', active);
      var shape = g.querySelector('circle, polygon, rect');
      if (!shape) return;
      shape.setAttribute('stroke', active ? '#ffffff' : 'rgba(0,0,0,0.35)');
      shape.setAttribute('stroke-width', active ? '3.2' : '1');
      if (shape.tagName === 'circle') {
        shape.setAttribute('r', String(active ? NODE_R + 1.8 : NODE_R));
      }
    });
  }

  function _scrollChatTo(msgId) {
    if (!msgId) return;
    var el = document.querySelector(
      '[data-chat-msg-id="' + (window.CSS && CSS.escape ? CSS.escape(msgId) : msgId) + '"]'
    );
    if (el && el.scrollIntoView) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  async function _checkout(msgId) {
    var convId = window.currentConvId;
    if (!convId || !msgId) return;
    // Clicking any node on a branch = switch to that branch's TIP.
    // This matches the "git checkout <branch>" mental model the user
    // asked for: one click = one branch switch, never mid-branch rewind.
    var target = _leafOfNode[msgId] || msgId;
    if (target === _currentHead) return;
    try {
      var r = await fetch('/api/chat/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conv_id: convId, msg_id: target }),
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
    if (!id) return;
    // On-branch click = navigate to that message (scroll chat + move
    // the white cursor). Off-branch click = checkout that branch tip.
    if (_headAncestorSet[id]) {
      _setCurrentView(id);
      _scrollChatTo(id);
    } else {
      _checkout(id);
    }
  });

  window.renderHistoryGraph = render;
  window.setHistoryCurrent = _setCurrentView;
})();
