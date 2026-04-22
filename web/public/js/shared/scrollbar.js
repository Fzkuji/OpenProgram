/**
 * Overlay scrollbars. Native scrollbar is hidden on `.scroll-overlay`
 * containers and a floating thumb is drawn on top instead. The thumb takes
 * no layout width — content extends edge to edge.
 */
(function () {
  'use strict';

  var SELECTORS = [
    '.chat-area',
    '.detail-body',
    '.sidebar-conversations',
    '.sidebar-conv-list',
    '.sidebar',
    '.pg-folders-nav',
    '.settings-content',
    '.live-exec-tree',
    '.main',
    '.code-modal-body',
  ];

  var FADE_DELAY = 900;
  var MIN_THUMB = 32;

  var installed = new WeakSet();

  function install(el) {
    if (installed.has(el)) return;
    installed.add(el);
    el.classList.add('scroll-overlay');

    var thumb = document.createElement('div');
    thumb.className = 'overlay-thumb';
    thumb.style.display = 'none';
    document.body.appendChild(thumb);

    var fadeTimer = null;
    var rafPending = false;
    var dragging = false;
    var dragStartY = 0;
    var dragStartScrollTop = 0;

    function show() {
      thumb.classList.add('visible');
      if (fadeTimer) clearTimeout(fadeTimer);
      fadeTimer = setTimeout(hide, FADE_DELAY);
    }
    function hide() {
      if (dragging) return;
      if (thumb.matches(':hover')) return;
      thumb.classList.remove('visible');
    }

    function apply(reveal) {
      rafPending = false;
      var rect = el.getBoundingClientRect();
      var sh = el.scrollHeight;
      var ch = el.clientHeight;
      var needs = sh > ch + 1 && rect.height > 0;
      if (!needs) {
        thumb.style.display = 'none';
        return;
      }
      var ratio = ch / sh;
      var thumbH = Math.max(MIN_THUMB, rect.height * ratio);
      var maxScroll = sh - ch;
      var scrollRatio = maxScroll > 0 ? (el.scrollTop / maxScroll) : 0;
      var top = rect.top + scrollRatio * (rect.height - thumbH);
      var right = window.innerWidth - rect.right + 2;
      thumb.style.display = '';
      thumb.style.height = thumbH + 'px';
      thumb.style.top = top + 'px';
      thumb.style.right = right + 'px';
      if (reveal) show();
    }

    function schedule(reveal) {
      if (rafPending) return;
      rafPending = true;
      requestAnimationFrame(function () { apply(reveal); });
    }

    el.addEventListener('scroll', function () { schedule(true); }, { passive: true });
    el.addEventListener('mouseenter', function () { schedule(true); });

    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(function () { schedule(false); }).observe(el);
    }
    if (typeof MutationObserver !== 'undefined') {
      new MutationObserver(function () { schedule(false); })
        .observe(el, { childList: true, subtree: true });
    }

    // Drag
    thumb.addEventListener('mousedown', function (e) {
      if (e.button !== 0) return;
      if (el.scrollHeight <= el.clientHeight + 1) return;
      dragging = true;
      dragStartY = e.clientY;
      dragStartScrollTop = el.scrollTop;
      thumb.classList.add('dragging');
      thumb.classList.add('visible');
      e.preventDefault();
    });

    function onDragMove(e) {
      if (!dragging) return;
      var rect = el.getBoundingClientRect();
      var sh = el.scrollHeight;
      var ch = el.clientHeight;
      if (sh <= ch + 1) return;
      var thumbH = Math.max(MIN_THUMB, rect.height * (ch / sh));
      var trackH = rect.height - thumbH;
      if (trackH <= 0) return;
      var maxScroll = sh - ch;
      var scrollDelta = ((e.clientY - dragStartY) / trackH) * maxScroll;
      el.scrollTop = dragStartScrollTop + scrollDelta;
    }
    function onDragEnd() {
      if (!dragging) return;
      dragging = false;
      thumb.classList.remove('dragging');
      if (fadeTimer) clearTimeout(fadeTimer);
      fadeTimer = setTimeout(hide, FADE_DELAY);
    }
    window.addEventListener('mousemove', onDragMove);
    window.addEventListener('mouseup', onDragEnd);

    thumb.addEventListener('mouseenter', function () {
      if (fadeTimer) clearTimeout(fadeTimer);
      thumb.classList.add('visible');
    });
    thumb.addEventListener('mouseleave', function () {
      if (dragging) return;
      if (fadeTimer) clearTimeout(fadeTimer);
      fadeTimer = setTimeout(hide, FADE_DELAY);
    });

    schedule(false);
  }

  function scan(root) {
    (root || document).querySelectorAll(SELECTORS.join(',')).forEach(install);
  }

  function init() {
    scan(document);
    // Re-scan occasionally to pick up dynamically added containers without
    // running a body-level MutationObserver that churns on every DOM change.
    setInterval(function () { scan(document); }, 2000);
    // Also update thumb positions on window resize
    window.addEventListener('resize', function () {
      document.querySelectorAll('.scroll-overlay').forEach(function (el) {
        el.dispatchEvent(new Event('scroll'));
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.installOverlayScrollbar = install;
})();
