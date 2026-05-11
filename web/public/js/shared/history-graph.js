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

  // Branch palette — desaturated pastels so multiple branches on
  // screen don't fight for attention. First slot is the HEAD branch
  // (rendered slightly brighter via on-head edge opacity / node size
  // difference, not by colour).
  var LANE_COLORS = [
    '#7aa2f7', // soft blue   — HEAD lane
    '#9ece6a', // sage green
    '#e0af68', // warm sand
    '#bb9af7', // muted lavender
    '#f7768e', // dusty rose
    '#7dcfff', // pale cyan
    '#ff9e64', // warm peach
    '#73daca', // mint
    '#c0caf5', // ice
    '#b4f9f8', // aqua
  ];

  var _currentHead = null;
  var _visibleIds = Object.create(null); // set of msgIds currently in viewport
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

  // Collapse a user-display-runtime node with its sole assistant-
  // display-runtime child into a single node in the graph. The chat
  // UI already merges that pair into one "restored runtime" card, so
  // showing two squares duplicates the same call visually. Keep the
  // assistant (its id is what the merged card stamps on data-msg-id,
  // so clicks / scroll-to-msg resolve correctly).
  //
  // Returns { graph, headId } — headId is remapped if it pointed at
  // a removed user-runtime node.
  function _collapseRuntimePairs(graph, headId) {
    if (!graph || !graph.length) return { graph: graph, headId: headId };
    var childrenOf = Object.create(null);
    graph.forEach(function (m) {
      if (m.parent_id) (childrenOf[m.parent_id] = childrenOf[m.parent_id] || []).push(m);
    });
    var removeIds = Object.create(null);
    var reparent = Object.create(null);       // asst id  -> new parent_id
    var userToAsst = Object.create(null);     // user id  -> asst id
    graph.forEach(function (m) {
      if (m.role !== 'user' || m.display !== 'runtime') return;
      var kids = childrenOf[m.id] || [];
      if (kids.length !== 1) return;
      var c = kids[0];
      if (c.role !== 'assistant' || c.display !== 'runtime') return;
      removeIds[m.id] = true;
      reparent[c.id] = m.parent_id || null;
      userToAsst[m.id] = c.id;
    });
    if (headId && userToAsst[headId]) headId = userToAsst[headId];
    var collapsed = [];
    graph.forEach(function (m) {
      if (removeIds[m.id]) return;
      if (m.id in reparent) {
        collapsed.push(Object.assign({}, m, { parent_id: reparent[m.id] }));
      } else {
        collapsed.push(m);
      }
    });
    return { graph: collapsed, headId: headId };
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

  // Compute shape size attrs for a given "isCurrent" state. Kept as a
  // separate function so _setCurrentView can swap sizes in-place
  // without touching fill/stroke.
  function _applyShapeSize(shape, isCurrent) {
    var r = isCurrent ? NODE_R + 1.8 : NODE_R;
    if (shape.tagName === 'circle') {
      shape.setAttribute('r', String(r));
    } else if (shape.tagName === 'polygon') {
      // Must mirror _buildShapeEl's formula — equilateral with
      // centroid at origin, so inner and outer triangles stay
      // concentric during size transitions.
      var t = r + 1.2;
      var COS30 = 0.8660254;
      shape.setAttribute(
        'points',
        '0,' + (-t)
          + ' ' + (t * COS30) + ',' + (t * 0.5)
          + ' ' + (-t * COS30) + ',' + (t * 0.5)
      );
    } else if (shape.tagName === 'rect') {
      var s = r - 0.2;
      shape.setAttribute('x', String(-s));
      shape.setAttribute('y', String(-s));
      shape.setAttribute('width', String(s * 2));
      shape.setAttribute('height', String(s * 2));
    }
  }

  // Build a shape element at an arbitrary base radius. Used both for
  // the full-size coloured node and for the shrunk white cursor that
  // rides on top of the current node.
  function _buildShapeEl(shape, color, r) {
    if (shape === 'circle') {
      return _svg('circle', { r: r, fill: color });
    } else if (shape === 'triangle') {
      // Equilateral triangle with circumradius t, centroid at origin.
      // Vertices at 90°, 210°, 330° on the circumscribing circle.
      // The old formula put the centroid at y ≈ 0.233t, which made
      // shrunk inner shapes look offset upward inside the larger
      // outer triangle (they shared origin, but not centre-of-mass).
      var t = r + 1.2;
      var COS30 = 0.8660254;
      return _svg('polygon', {
        points: '0,' + (-t)
          + ' ' + (t * COS30) + ',' + (t * 0.5)
          + ' ' + (-t * COS30) + ',' + (t * 0.5),
        fill: color,
      });
    } else if (shape === 'square') {
      var s = r - 0.2;
      return _svg('rect', {
        x: -s, y: -s, width: s * 2, height: s * 2, rx: 0.8, ry: 0.8, fill: color,
      });
    }
    return null;
  }

  function _appendShape(parent, shape, color) {
    // Solid coloured shape, no outline. "Visible in viewport" state
    // is applied later by _applyVisibility: bumps the outer shape up
    // a size and stacks a shape-matched white inner shape on top to
    // produce the hollow-centre look.
    var el = _buildShapeEl(shape, color, NODE_R);
    if (el) parent.appendChild(el);
  }

  // Radius used for the white cursor shape — ~55% of the node radius
  // so it fits cleanly inside the bumped-up current node.
  var CURSOR_R = NODE_R * 0.55;

  function _shapeTypeFromTag(tagName) {
    if (tagName === 'polygon') return 'triangle';
    if (tagName === 'rect') return 'square';
    return 'circle';
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
    // Collapse user-runtime+asst-runtime pairs first so the rest of
    // the renderer — signature, tree build, head-ancestor walk — sees
    // the deduped graph.
    var collapsed = _collapseRuntimePairs(graph, headId);
    graph = collapsed.graph;
    headId = collapsed.headId;

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

    // Nodes — all built at default (non-visible) size and no inner.
    // _recomputeVisibility() below stamps the highlight state based
    // on what's actually on-screen.
    Object.keys(tree.byId).forEach(function (id) {
      var node = tree.byId[id];
      var p = pos(node);
      var isHead = id === headId;
      var onHead = !!headAncestors[id];
      var color = _laneColor(node._lane);
      var g = _svg('g', {
        class: 'history-node' + (isHead ? ' is-head' : '') + (onHead ? '' : ' off-head'),
        transform: 'translate(' + p.x + ',' + p.y + ')',
        'data-msg-id': id,
      });
      // Invisible hit-target: extends the click region way past the
      // tiny visual node so a quick mouse aim still lands on the
      // right node. NODE_R is 5px; HIT_R 14 gives roughly the row
      // height of a comfortable click target without overlap.
      var hit = _svg('circle', {
        r: '14',
        fill: 'transparent',
        'pointer-events': 'all',
      });
      g.appendChild(hit);
      g.style.cursor = 'pointer';
      var r = onHead ? NODE_R : NODE_R * 0.7;
      var el = _buildShapeEl(_shapeFor(node), color, r);
      if (el) {
        el.setAttribute('pointer-events', 'none');  // hit goes through to .history-node
        g.appendChild(el);
      }
      g._nodeData = node;
      nodeG.appendChild(g);
    });

    // Branch-name tags — git-style labels floating above leaves that
    // the user explicitly named. Source of truth is
    // window._branchesByConv (populated by conversations.js after
    // every list_branches reply); we read whatever's cached for the
    // current session.
    (function _drawBranchTags() {
      var sid = window.currentSessionId;
      var rows = (window._branchesByConv && window._branchesByConv[sid]) || [];
      var named = rows.filter(function (r) { return r.is_named && r.name; });
      if (!named.length) return;
      var tagG = _svg('g', { class: 'history-branch-tags' });
      named.forEach(function (b) {
        var node = tree.byId[b.head_msg_id];
        if (!node) return;
        var p = pos(node);
        var label = b.name;
        // Approx text width: 7.2px per char at 11px font.
        var textW = Math.ceil(label.length * 7.2);
        var pad = 6;
        var w = textW + pad * 2;
        var h = 16;
        var dy = -22;  // float above the node bubble
        var tg = _svg('g', {
          class: 'history-branch-tag',
          transform: 'translate(' + p.x + ',' + p.y + ')',
        });
        var rect = _svg('rect', {
          x: String(-w / 2),
          y: String(dy - h / 2),
          width: String(w),
          height: String(h),
          rx: '3',
          fill: '#3aafa9',
        });
        var text = _svg('text', {
          x: '0',
          y: String(dy + 4),
          'text-anchor': 'middle',
          'font-size': '11',
          'font-family': 'var(--font-sans, sans-serif)',
          fill: '#fff',
        });
        text.textContent = label;
        tg.appendChild(rect);
        tg.appendChild(text);
        tagG.appendChild(tg);
      });
      svg.appendChild(tagG);
    })();

    body.replaceChildren(svg);
    _tooltip = null;

    // The new SVG has no inner-white shapes; reset the tracked set
    // so the upcoming recompute treats every on-screen bubble as
    // "newly visible" and creates the inner shapes.
    _visibleIds = Object.create(null);

    // First render after #chatArea mounts is a good moment to hook
    // chat scroll → visibility sync. Idempotent.
    _wireChatScrollSync();
    _recomputeVisibility();

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

  // Apply visibility state to a single node group: bumps size on the
  // outer coloured shape + adds/removes a shape-matched white inner
  // shape that fades in/out.
  function _applyVisibility(nodeEl, visible) {
    var shape = nodeEl.querySelector('circle, polygon, rect');
    if (shape) _applyShapeSize(shape, visible);
    var inner = nodeEl.querySelector('.n-inner');
    if (visible) {
      if (!inner && shape) {
        var shapeType = _shapeTypeFromTag(shape.tagName);
        inner = _buildShapeEl(shapeType, '#ffffff', CURSOR_R);
        if (inner) {
          inner.setAttribute('class', 'n-inner');
          inner.setAttribute(
            'style',
            'opacity: 0; transition: opacity 180ms ease; pointer-events: none;'
          );
          nodeEl.appendChild(inner);
          // Fade in on next frame so the transition actually runs.
          var el = inner;
          requestAnimationFrame(function () {
            el.setAttribute(
              'style',
              'opacity: 1; transition: opacity 180ms ease; pointer-events: none;'
            );
          });
        }
      }
    } else if (inner) {
      // Remove immediately — fade-out is less important than keeping
      // the DOM clean when many bubbles exit the viewport at once.
      inner.parentNode.removeChild(inner);
    }
  }

  // Diff-update the highlight state across all nodes.
  function _setVisibleSet(newSet) {
    var panel = document.getElementById('historyPanel');
    if (!panel) return;
    var body = panel.querySelector('.history-body');
    if (!body) return;
    body.querySelectorAll('.history-node').forEach(function (g) {
      var id = g.getAttribute('data-msg-id');
      var nowVisible = !!newSet[id];
      var wasVisible = !!_visibleIds[id];
      if (nowVisible !== wasVisible) _applyVisibility(g, nowVisible);
    });
    _visibleIds = newSet;
  }

  // Compute which chat bubbles intersect #chatArea's viewport and
  // push that set to the graph. A bubble may carry data-msg-ids
  // (space-separated list) when it represents more than one
  // underlying message — e.g. a restored runtime block that merges
  // the user-call + assistant-result pair. Every listed id lights
  // up so both graph squares reflect the on-screen state.
  function _recomputeVisibility() {
    var area = document.getElementById('chatArea');
    if (!area) return;
    var container = document.getElementById('chatMessages');
    if (!container) return;
    var rect = area.getBoundingClientRect();
    var bubbles = container.querySelectorAll(
      ':scope > [data-msg-id], :scope > [data-msg-ids]'
    );
    var newSet = Object.create(null);
    for (var i = 0; i < bubbles.length; i++) {
      var br = bubbles[i].getBoundingClientRect();
      if (br.bottom <= rect.top || br.top >= rect.bottom) continue;
      var multi = bubbles[i].getAttribute('data-msg-ids');
      if (multi) {
        var parts = multi.split(/\s+/);
        for (var j = 0; j < parts.length; j++) {
          if (parts[j]) newSet[parts[j]] = true;
        }
      } else {
        var single = bubbles[i].getAttribute('data-msg-id');
        if (single) newSet[single] = true;
      }
    }
    _setVisibleSet(newSet);
  }

  function _chatBubbleFor(msgId) {
    if (!msgId) return null;
    // The chat renders into #chatMessages (vanilla conversations.js).
    // Each bubble has data-msg-id — same attribute name the graph
    // uses on its own nodes, so we MUST scope to #chatMessages or
    // we'd accidentally match a graph node living inside the right
    // sidebar.
    var container = document.getElementById('chatMessages');
    if (!container) return null;
    var sel = '[data-msg-id="'
      + (window.CSS && CSS.escape ? CSS.escape(msgId) : msgId)
      + '"]';
    return container.querySelector(sel);
  }

  function _scrollChatTo(msgId) {
    var bubble = _chatBubbleFor(msgId);
    if (!bubble) return;
    // scrollIntoView walks up to the nearest scrollable ancestor,
    // which is #chatArea — that's what we want.
    bubble.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // Attached to #chatArea on first run; survives conversation switches
  // because the element persists across /chat ↔ /c/:id navigations.
  var _chatScrollWired = false;
  function _wireChatScrollSync() {
    if (_chatScrollWired) return;
    var area = document.getElementById('chatArea');
    if (!area) return;
    _chatScrollWired = true;
    var raf = 0;
    area.addEventListener('scroll', function () {
      if (raf) return;
      raf = requestAnimationFrame(function () {
        raf = 0;
        _recomputeVisibility();
      });
    }, { passive: true });
  }

  async function _checkout(msgId) {
    var sessionId = window.currentSessionId;
    if (!sessionId || !msgId) return;
    // Clicking any node on a branch = switch to that branch's TIP.
    // This matches the "git checkout <branch>" mental model the user
    // asked for: one click = one branch switch, never mid-branch rewind.
    var target = _leafOfNode[msgId] || msgId;
    if (target === _currentHead) return;
    try {
      var r = await fetch('/api/chat/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, msg_id: target }),
      });
      if (!r.ok) throw new Error(await r.text());
      if (window.ws && window.ws.readyState === WebSocket.OPEN) {
        window.ws.send(JSON.stringify({ action: 'load_session', session_id: sessionId }));
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
    // On-branch click: scroll chat to that message. Visibility sync
    // on scroll end will light up the right set of graph nodes.
    // Off-branch click: checkout that branch tip (git-style).
    if (_headAncestorSet[id]) {
      _scrollChatTo(id);
    } else {
      _checkout(id);
    }
  });

  // Cache the last input so external triggers (e.g. branch rename) can
  // repaint without round-tripping through load_session.
  var _lastGraph = null;
  var _lastHeadId = null;
  var _origRender = render;
  function _renderAndCache(graph, headId) {
    _lastGraph = graph;
    _lastHeadId = headId;
    return _origRender(graph, headId);
  }
  window.renderHistoryGraph = _renderAndCache;
  window.repaintBranchTags = function () {
    if (_lastGraph) _origRender(_lastGraph, _lastHeadId);
  };
  window.recomputeHistoryVisibility = _recomputeVisibility;
})();
