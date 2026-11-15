import assert from 'node:assert/strict';
import test from 'node:test';
import { canvasCssGeometry, canvasGeometry, canvasViewport, presetLayout, leaves } from '../../lib/tabs/canvas/canvas-layout.ts';

// Resolves calc(P% + Npx) against a containing length.
const resolve = (css, size) => {
  const m = /^calc\((-?[\d.]+)% \+ (-?[\d.]+)px\)$/.exec(css);
  assert.ok(m, `calc form: ${css}`);
  return Number(m[1]) / 100 * size + Number(m[2]);
};

test('panes and dividers written as shares of the canvas land where the pixel layout puts them, at any canvas size', () => {
  const layout = presetLayout('main+2', ['a', 'b', 'c']);
  const css = canvasCssGeometry(layout.root);
  for (const [width, height] of [[1305, 889], [1080, 700], [777, 512]]) {
    const px = canvasGeometry(layout.root, canvasViewport(width, height));
    for (const pane of leaves(layout.root)) {
      const a = px.panes.get(pane.id), b = css.panes.get(pane.id);
      assert.ok(Math.abs(resolve(b.left, width) - a.left) < 0.01);
      assert.ok(Math.abs(resolve(b.width, width) - a.width) < 0.01);
      assert.ok(Math.abs(resolve(b.top, height) - a.top) < 0.01);
      assert.ok(Math.abs(resolve(b.height, height) - a.height) < 0.01);
    }
    px.dividers.forEach((d, i) => assert.ok(Math.abs(resolve(css.dividers[i].left, width) - d.left) < 0.01));
  }
});

test('the inset of a split canvas comes off every side', () => {
  const layout = presetLayout('main+2', ['a', 'b', 'c']);
  const [pane] = leaves(layout.root);
  const plain = canvasCssGeometry(layout.root).panes.get(pane.id), inset = canvasCssGeometry(layout.root, 1).panes.get(pane.id);
  assert.ok(Math.abs(resolve(inset.left, 1200) - resolve(plain.left, 1200) - 1) < 0.01);
  assert.ok(Math.abs(resolve(plain.width, 1200) - resolve(inset.width, 1200) - 2) < 0.01);
});

// The strip's sizing, read as data from the center area's CSS modules: the
// last declaration of a property in a rule with exactly this selector.
const stripCss = await import('node:fs').then(({ readFileSync }) => [
  'components/center-tabs/strip/tab-strip.module.css',
  'components/center-tabs/browser/browser.module.css',
  'components/center-tabs/pages/pages.module.css',
].map((path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8')).join('\n'));
const stripRoot = (await import('postcss')).default.parse(stripCss);
function finalDeclaration(selector, property) {
  let value;
  stripRoot.walkRules((rule) => {
    if (!rule.selectors?.includes(selector)) return;
    rule.walkDecls(property, (declaration) => { value = declaration.value; });
  });
  return value;
}

test('desktop tabs and split tabs are 240px wide, shrinking from that basis', () => {
  for (const selector of [':global(html.is-desktop) .tabsFlow > .tab', ':global(html.is-desktop) .tabsFlow > .compoundTab']) {
    assert.equal(finalDeclaration(selector, 'width'), '240px');
    assert.equal(finalDeclaration(selector, 'flex'), '0 1 240px');
    assert.equal(finalDeclaration(selector, 'max-width'), '240px');
  }
  assert.equal(finalDeclaration('.compoundTab', 'flex'), '0 1 200px', 'browser mode keeps the 200px basis');
  assert.equal(finalDeclaration('.compoundTab', 'min-width'), '48px');
  assert.equal(finalDeclaration('.compoundMembers', 'display'), 'flex');
});

test('the desktop strip is a 40px band whose tab flow leaves room for add, overview and the main menu', () => {
  assert.equal(finalDeclaration(':global(html.is-desktop) .strip', 'height'), '40px');
  assert.equal(finalDeclaration(':global(html.is-desktop) .strip', 'align-items'), 'center');
  assert.equal(finalDeclaration(':global(html.is-desktop) .tabsFlow', 'align-items'), 'center');
  assert.equal(finalDeclaration(':global(html.is-desktop) .tabsFlow', 'max-width'), 'calc(100% - 108px)');
});

test('tabs, the active tab and split tabs share the small button height and radius, without a shadow', () => {
  for (const selector of [':global(html.is-desktop) .tab', ':global(html.is-desktop) .tab.tabActive', ':global(html.is-desktop) .compoundTab', ':global(html.is-desktop) .compoundTabActive']) {
    assert.equal(finalDeclaration(selector, 'height'), 'var(--ui-button-h-sm)', selector);
  }
  for (const selector of [':global(html.is-desktop) .tab.tabActive', ':global(html.is-desktop) .compoundTabActive']) {
    assert.equal(finalDeclaration(selector, 'border-radius'), 'var(--radius-md)', selector);
    assert.equal(finalDeclaration(selector, 'box-shadow'), 'none', selector);
  }
});

test('the close, new-tab and main-menu controls use the shared control sizes', () => {
  assert.equal(finalDeclaration('.tabClose', 'width'), 'var(--ui-control-inline)');
  assert.equal(finalDeclaration('.tabClose', 'height'), 'var(--ui-control-inline)');
  for (const selector of ['.plusBtn', '.menuBtn']) {
    assert.equal(finalDeclaration(selector, 'width'), 'var(--ui-button-h-sm)', selector);
    assert.equal(finalDeclaration(selector, 'height'), 'var(--ui-button-h-sm)', selector);
  }
  assert.equal(finalDeclaration('.menuBtn', 'margin-left'), 'auto', 'the main menu owns the strip\'s right end');
});

test('a new tab grows in with the 180ms enter animation', () => {
  assert.equal(finalDeclaration('.tabEnter', 'animation'), 'tabEnter 180ms ease-out');
});

test('a dragged tab is an opaque drag surface, and stays opaque and lifted while leaving the strip', () => {
  for (const selector of ['.tab[data-pointer-drag="true"]', '.compoundTab[data-pointer-drag="true"]']) {
    assert.equal(finalDeclaration(selector, 'background'), 'var(--drag-surface-background)', selector);
    assert.equal(finalDeclaration(selector, 'backdrop-filter'), 'var(--drag-surface-backdrop)', selector);
    assert.equal(finalDeclaration(selector, 'opacity'), '1', selector);
  }
  assert.equal(finalDeclaration('.tab[data-detach-intent="true"]', 'opacity'), '1');
  assert.equal(finalDeclaration('.tab[data-detach-intent="true"]', 'z-index'), '40');
});
