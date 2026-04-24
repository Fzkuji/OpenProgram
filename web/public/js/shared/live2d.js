// Anime mascots — two anime-style images perched on the left and
// right edges of the chat input bar, each a different random waifu
// from api.waifu.pics. Breathing animation via CSS.
//
// Positioned inside .input-area (the wrapper around the input +
// button row) so they stay pinned to the bar regardless of sidebar
// collapse / window size. Bottom edge flush with the top of the
// wrapper so they visually "stand on" the input.

(function () {
  if (window.__waifuLoaded) return;
  window.__waifuLoaded = true;

  var style = document.createElement('style');
  style.textContent =
    '.input-area { position: relative; }' +
    '.waifu-mascot {' +
      'position: absolute;' +
      'bottom: 100%;' +
      'width: 110px;' +
      'height: 150px;' +
      'pointer-events: none;' +
      'z-index: 15;' +
      'border-radius: 12px 12px 4px 4px;' +
      'overflow: hidden;' +
      'box-shadow: 0 -4px 14px rgba(0,0,0,0.14);' +
      'opacity: 0;' +
      'transition: opacity 400ms ease;' +
      'transform-origin: center bottom;' +
    '}' +
    '.waifu-mascot.ready { opacity: 1; }' +
    '.waifu-mascot.left  { left: 8px;  animation: waifu-idle-l 4.2s ease-in-out infinite; }' +
    '.waifu-mascot.right { right: 8px; animation: waifu-idle-r 4.6s ease-in-out infinite; }' +
    '.waifu-mascot img {' +
      'width: 100%; height: 100%; object-fit: cover; display: block;' +
    '}' +
    '@keyframes waifu-idle-l {' +
      '0%, 100% { transform: translateY(0) rotate(0); }' +
      '50%     { transform: translateY(-3px) rotate(-0.8deg); }' +
    '}' +
    '@keyframes waifu-idle-r {' +
      '0%, 100% { transform: translateY(0) rotate(0); }' +
      '50%     { transform: translateY(-3px) rotate(0.8deg); }' +
    '}';
  document.head.appendChild(style);

  function _mountOne(parent, side) {
    var el = document.createElement('div');
    el.className = 'waifu-mascot ' + side;
    var img = document.createElement('img');
    img.alt = '';
    img.addEventListener('load', function () { el.classList.add('ready'); });
    img.addEventListener('error', function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    });
    el.appendChild(img);
    parent.appendChild(el);

    fetch('https://api.waifu.pics/sfw/waifu')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data && data.url) img.src = data.url;
        else throw new Error('no url');
      })
      .catch(function (err) {
        console.error('[waifu]', side, err);
        if (el.parentNode) el.parentNode.removeChild(el);
      });
  }

  function _tryMount() {
    var parent = document.querySelector('.input-area');
    if (!parent) return false;
    if (parent.querySelector('.waifu-mascot')) return true; // already mounted
    _mountOne(parent, 'left');
    _mountOne(parent, 'right');
    return true;
  }

  // PageShell mounts input-area async on the chat route. Poll briefly
  // until it appears; give up after a few seconds so we don't spin
  // forever on non-chat routes.
  if (!_tryMount()) {
    var tries = 0;
    var t = setInterval(function () {
      tries++;
      if (_tryMount() || tries > 40) clearInterval(t);
    }, 150);
  }
})();
