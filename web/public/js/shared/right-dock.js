// Right sidebar controller — mirrors the left sidebar. The <aside> is
// always in the flex row; `.collapsed` shrinks it to the icon-only
// rail (48px wide, same as left); `data-view` picks which view
// (history | detail) fills the expanded content area.
//
// Public: window.rightDock.{show, close, toggle}
//   show(view)   — expand, set active view
//   close()      — collapse (view state preserved so toggle can restore)
//   toggle(view) — click on a nav icon:
//                    * collapsed       → expand to `view`
//                    * expanded, same  → collapse
//                    * expanded, diff  → switch view
//                  no-arg toggle just collapses/expands at current view.
//
// Legacy shims keep the existing ui.js callers (toggleDetail /
// closeDetail / showDetail) working without edits.

(function () {
  function _el() { return document.getElementById('rightSidebar'); }
  function _collapsed(el) { return el.classList.contains('collapsed'); }

  function _syncNav(el) {
    var cur = el.getAttribute('data-view');
    el.querySelectorAll('.right-nav-item').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-view') === cur);
    });
  }

  function show(view) {
    var el = _el();
    if (!el) return;
    if (view) el.setAttribute('data-view', view);
    el.classList.remove('collapsed');
    _syncNav(el);
  }

  function close() {
    var el = _el();
    if (!el) return;
    el.classList.add('collapsed');
  }

  function toggle(view) {
    var el = _el();
    if (!el) return;
    if (!view) {
      // Bare toggle — flip collapsed state, keep current view.
      if (_collapsed(el)) show();
      else close();
      return;
    }
    var cur = el.getAttribute('data-view');
    if (_collapsed(el)) show(view);
    else if (cur === view) close();
    else show(view);
  }

  window.rightDock = { show: show, close: close, toggle: toggle };

  // Legacy shims used by ui.js.
  window.toggleDetail = function () { toggle('detail'); };
  window.closeDetail = function () {
    try { if (typeof selectedPath !== 'undefined') selectedPath = null; } catch (e) {}
    close();
  };
  window.toggleHistoryPanel = function () { toggle('history'); };
  window.openHistoryPanel = function () { show('history'); };
  window.closeHistoryPanel = function () { close(); };
})();
