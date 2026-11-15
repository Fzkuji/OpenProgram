import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { registerHooks } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import ts from 'typescript';
import React, { act, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { parseHTML } from 'linkedom';
const rootURL = new URL('../../../', import.meta.url);
const hooks = registerHooks({
    resolve(s, c, next) {
        const mocks = { '@/lib/i18n': 'export const useTranslation=()=>({text:x=>x});', '@/components/files/file-type-icon': 'export const FileTypeIcon=()=>null;', '@/components/solar-icons': 'export const SolarIcon=()=>null;', '@/components/center-tabs/strip/tab-favicon': 'export const TabFavicon=()=>null;' };
        if (mocks[s])
            return { url: 'data:text/javascript,' + encodeURIComponent(mocks[s]), shortCircuit: true };
        const base = s.startsWith('@/') ? new URL(s.slice(2), rootURL) : s.startsWith('.') ? new URL(s, c.parentURL) : null;
        if (base)
            for (const ext of ['.ts', '.tsx'])
                if (existsSync(new URL(base.href + ext)))
                    return { url: base.href + ext, shortCircuit: true };
        return next(s, c);
    },
    load(url, c, next) { if (url.endsWith('.tsx'))
        return { format: 'module', shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), 'utf8'), { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext } }).outputText }; return next(url, c); }
});
after(() => hooks.deregister());
const { useFileMention } = await import(new URL('components/chat/composer/attach/mention/use-file-mention.ts', rootURL));
const { useComposerKeyDown } = await import(new URL('components/chat/composer/input/use-composer-keydown.ts', rootURL));
const { FileMenu } = await import(new URL('components/chat/composer/attach/mention/file-menu.tsx', rootURL));
async function fixture(t, run) {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const keys = ['window', 'document', 'HTMLElement', 'fetch', 'requestAnimationFrame', 'IS_REACT_ACT_ENVIRONMENT', 'innerWidth', 'innerHeight'];
    const previous = Object.fromEntries(keys.map(k => [k, Object.getOwnPropertyDescriptor(globalThis, k)]));
    const { window, document } = parseHTML('<html><body></body></html>');
    window.innerWidth = 1280;
    window.innerHeight = 800;
    const requests = [];
    let latest;
    let submits = 0;
    const frames = [];
    Object.assign(globalThis, { window, document, HTMLElement: window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true, requestAnimationFrame: fn => frames.push(fn), fetch: url => new Promise(resolve => requests.push({ url, resolve })) });
    function Probe() {
        const [input, setInput] = useState('');
        const ref = useRef(null);
        const mention = useFileMention({ input, setInput, textareaRef: ref });
        const keydown = useComposerKeyDown({ input, setInput, fileMention: mention, historyRecall: { historyIndex: -1, recallPrevious: () => false }, slash: { visible: false }, selectSlashCommand: () => { throw Error('unexpected slash'); }, submit: () => { submits++; } });
        latest = { mention, input, setInput, keydown };
        return React.createElement(React.Fragment, null, React.createElement('textarea', { ref, value: input, onChange: e => setInput(e.target.value), onKeyDown: keydown }), React.createElement(FileMenu, { items: mention.fileMatches, selectedIndex: mention.fileMenuIndex, position: mention.fileMenuPos, onHover: mention.setFileMenuIndex, onPick: mention.pickFile, loading: mention.fileMenuLoading, query: mention.atToken?.partial ?? '' }));
    }
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    try {
        await act(() => root.render(React.createElement(Probe)));
        await run({ requests, submits: () => submits, read: () => latest, set: async (input) => act(() => { latest.setInput(input); latest.mention.setCaretPos(input.length); }), tick: async () => act(() => t.mock.timers.tick(100)), reply: async (i, path) => act(() => requests[i].resolve(Response.json({ matches: [{ path, is_dir: false }] }))), key: async (key, extra = {}) => { let prevented = false; await act(() => latest.keydown({ key, nativeEvent: {}, currentTarget: host.querySelector('textarea'), preventDefault() { prevented = true; }, ...extra })); return prevented; }, document });
    }
    finally {
        await act(() => root.unmount());
        host.remove();
        t.mock.timers.reset();
        for (const k of keys)
            if (previous[k]) Object.defineProperty(globalThis, k, previous[k]); else delete globalThis[k];
    }
}
test('new query immediately invalidates prior options and pending Enter or Tab cannot send', t => fixture(t, async (f) => {
    await f.set('@alpha');
    await f.tick();
    await f.reply(0, 'alpha.txt');
    await f.set('@beta');
    assert.equal(f.read().mention.fileMatches.length, 0);
    assert.equal(await f.key('Enter'), true);
    assert.equal(await f.key('Tab'), true);
    assert.equal(f.read().input, '@beta');
    assert.equal(f.submits(), 0);
    await f.tick();
    await f.reply(1, 'beta.txt');
    await f.key('Enter');
    assert.equal(f.read().input, '@beta.txt ');
    assert.equal(f.submits(), 0);
}));
test('Escape invalidates the pending query and keeps its late result dismissed', t => fixture(t, async (f) => {
    await f.set('@alpha');
    await f.tick();
    await f.reply(0, 'alpha.txt');
    await f.set('@beta');
    await f.tick();
    assert.equal(await f.key('Escape'), true);
    assert.equal(f.read().mention.fileMenuPos, null);
    await f.reply(1, 'beta.txt');
    assert.equal(Boolean(f.document.querySelector('[role=listbox]')), false);
    assert.equal(f.read().mention.fileMatches.length, 0);
    await f.key('Enter');
    assert.equal(f.read().input, '@beta');
    assert.equal(f.submits(), 1);
    await f.set('@gamma');
    await f.tick();
    await f.reply(2, 'gamma.txt');
    await f.key('Tab');
    assert.equal(f.read().input, '@gamma.txt ');
    await f.set('@beta');
    await f.tick();
    await f.reply(3, 'beta.txt');
    await f.key('Enter');
    assert.equal(f.read().input, '@beta.txt ');
}));
test('initial pending search blocks only completion keys and preserves Shift Enter and IME', t => fixture(t, async (f) => {
    await f.set('@alpha');
    assert.equal(await f.key('Enter'), true);
    assert.equal(await f.key('Tab'), true);
    assert.equal(await f.key('Enter', { shiftKey: true }), false);
    assert.equal(await f.key('Enter', { nativeEvent: { isComposing: true, keyCode: 229 } }), false);
    assert.equal(f.submits(), 0);
    assert.equal(f.read().input, '@alpha');
    assert.equal(await f.key('Escape'), true);
    assert.equal(await f.key('Tab'), false);
    await f.key('Enter');
    assert.equal(f.submits(), 1);
}));
test('completed empty and failed searches retain ordinary Enter and native Tab behavior', t => fixture(t, async (f) => {
    for (const failure of [false, true]) {
        await f.set(failure ? '@failure' : '@empty');
        await f.tick();
        await act(async () => {
            f.requests.at(-1).resolve(failure ? new Response('', { status: 500 }) : Response.json({ matches: [] }));
        });
        assert.equal(f.read().mention.fileMenuLoading, false);
        assert.equal(await f.key('Tab'), false);
        await f.key('Enter');
    }
    assert.equal(f.submits(), 2);
}));
test('an obsolete response cannot replace the new query results', t => fixture(t, async (f) => {
    await f.set('@old');
    await f.tick();
    await f.set('@new');
    await f.tick();
    await f.reply(1, 'new.txt');
    await f.reply(0, 'old.txt');
    assert.deepEqual(f.read().mention.fileMatches.map(item => item.path), ['new.txt']);
    await f.key('Enter');
    assert.equal(f.read().input, '@new.txt ');
}));

test('Escape closes completed empty and failed search menus', t => fixture(t, async (f) => {
    for (const failure of [false, true]) {
        await f.set(failure ? '@failure' : '@empty');
        await f.tick();
        await act(async () => { f.requests.at(-1).resolve(failure ? new Response('', { status: 500 }) : Response.json({ matches: [] })); });
        assert.ok(f.document.querySelector('[role=listbox]'));
        assert.equal(await f.key('Escape'), true);
        assert.equal(f.document.querySelector('[role=listbox]'), null);
        assert.equal(f.read().mention.fileMatches.length, 0);
    }
    assert.equal(f.submits(), 0);
}));
