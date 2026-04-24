// Live2D mascot — oh-my-live2d + Shizuku from jsDelivr.
// Anchored inside .input-area so it sits right above the chat input
// bar (instead of floating in the viewport corner).

(function () {
  if (window.__live2dLoaded) return;
  window.__live2dLoaded = true;

  function _init() {
    var loader = window.loadOml2d
      || (window.OML2D && window.OML2D.loadOml2d)
      || (window.oml2d && window.oml2d.loadOml2d);
    if (typeof loader !== 'function') {
      console.error('[live2d] loadOml2d not found');
      return;
    }
    var parent = document.querySelector('.input-area');
    if (!parent) return false;
    // Stage is absolutely positioned inside the input area; ensure
    // .input-area establishes a positioning context.
    if (!parent.style.position) parent.style.position = 'relative';
    try {
      loader({
        parentElement: parent,
        mobileDisplay: true,
        models: [{
          name: 'pio',
          path: 'https://fastly.jsdelivr.net/gh/fghrsh/live2d_api/model/Potion-Maker/Pio/model.json',
          position: [0, 20],
          scale: 0.6,
          stageStyle: {
            width: 160,
            height: 200,
          },
        }],
        menus: { disable: true },
        tips: { idleTips: { interval: 20000 } },
        // Stage sits above the input bar, tucked at the right edge
        // so it doesn't eat the textarea space.
        dockedPosition: 'right',
        sayHello: false,
      });
      // Force-position the stage container after oml2d creates it.
      setTimeout(function () {
        var stage = parent.querySelector('#oml2d-stage, .oml2d-stage, [class*="oml2d"]');
        if (stage) {
          stage.style.position = 'absolute';
          stage.style.bottom = '100%';
          stage.style.right = '8px';
          stage.style.left = 'auto';
          stage.style.top = 'auto';
          stage.style.pointerEvents = 'none';
          stage.style.zIndex = '15';
        }
      }, 800);
    } catch (err) {
      console.error('[live2d] init failed:', err);
    }
    return true;
  }

  function _load() {
    var s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/oh-my-live2d@latest';
    s.async = true;
    s.onload = function () {
      if (_init() === false) {
        // .input-area not mounted yet; poll briefly.
        var tries = 0;
        var t = setInterval(function () {
          tries++;
          if (_init() !== false || tries > 40) clearInterval(t);
        }, 150);
      }
    };
    s.onerror = function (e) { console.error('[live2d] CDN load failed', e); };
    document.head.appendChild(s);
  }

  _load();
})();
