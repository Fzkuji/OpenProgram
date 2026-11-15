import { registerHooks } from 'node:module';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { createElement, act } from 'react';
import { createRoot } from 'react-dom/client';
import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { parseHTML } from 'linkedom';
const base = new URL('../../../', import.meta.url);
const mocks = {
    '@/lib/abilities/skills-store': 'export const useSkills=()=>({detail:globalThis.auditSkill,deleteSkill:()=>{}});',
    '@/lib/runtime-bridge/dom/markdown-render': 'export const typesetMath=e=>{globalThis.auditMathText=e.textContent};',
    '@/lib/i18n': 'export const useTranslation=()=>({t:x=>x,text:x=>x});',
    '@/components/ui/button': 'export const Button=()=>null;',
    '@/lib/session-store': 'export const useSessionStore=selector=>selector(globalThis.auditState); export const selectViewRows=()=>state=>state.rowsById;',
    '@/lib/chat/history/chat-scroll': 'export const whenAreaScrollSettles=()=>{};',
    '@/lib/files/files-shared': 'export const absRawFileUrl=()=>{throw Error("No file access expected")};',
    '@/lib/nav/shallow-nav': 'export const pushPath=()=>{};',
    'next/navigation': 'export const useRouter=()=>({push(){}});',
    'next/link': 'export default function Link(){return null;}',
    './attachment-preview': 'export const AttachmentPreview=()=>{throw Error("No attachment should render")};',
    '../turn-files/turn-files-chips': 'export const TurnFilesChips=()=>{throw Error("No files should render")};',
};
const loaderHooks = registerHooks({ resolve(s, c, next) {
        if (s.endsWith('.css'))
            return { url: 'data:text/javascript,export default {}', shortCircuit: true };
        if (Object.hasOwn(mocks, s))
            return { url: 'data:text/javascript,' + encodeURIComponent(mocks[s]), shortCircuit: true };
        if (s.startsWith('@/')) {
            const u = new URL(s.slice(2), base);
            for (const e of ['.ts', '.tsx'])
                if (existsSync(fileURLToPath(u) + e))
                    return { url: u.href + e, shortCircuit: true };
        }
        if (s.startsWith('.') && !/\.[a-z]+$/.test(s)) {
            const u = new URL(s, c.parentURL);
            for (const e of ['.ts', '.tsx'])
                if (existsSync(fileURLToPath(u) + e))
                    return { url: u.href + e, shortCircuit: true };
        }
        return next(s, c);
    }, load(u, c, next) { if (u.endsWith('.tsx'))
        return { format: 'module', source: ts.transpileModule(readFileSync(new URL(u), 'utf8'), { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText, shortCircuit: true }; return next(u, c); } });
after(() => loaderHooks.deregister());

test('untrusted message previews and skill fetch render text without injected elements', async () => {
    const names = ['fetch', 'document', 'window', 'HTMLElement', 'IS_REACT_ACT_ENVIRONMENT', 'auditState', 'auditSkill', 'auditMathText'];
    const originals = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
    let root;
    let host;
    try {
        const { document, window } = parseHTML('<html><body></body></html>');
        Object.assign(globalThis, { document, window, HTMLElement: window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true });
        host = document.createElement('div');
        document.body.append(host);
        root = createRoot(host);
        globalThis.auditState = { currentSessionId: 'synthetic', setComposerInput() { }, messageOrder: { synthetic: ['u1', 'a1', 'u2'] }, rowsById: {
                u1: { id: 'u1', role: 'user', rowKind: 'user', content: '**ordinary user** <form data-audit="rail-user"><input value="synthetic"></form>' },
                a1: { id: 'a1', role: 'assistant', rowKind: 'assistant', status: 'completed', content: '$<input data-audit="rail-assistant" value="synthetic">$' },
                u2: { id: 'u2', role: 'user', rowKind: 'user', content: 'second message' }
            } };
        const { MessageRail } = await import(new URL('components/chat/messages/navigation/message-rail.tsx', base));
        await act(() => root.render(createElement(MessageRail)));
        const rail = host.querySelector('.msg-rail');
        assert.ok(rail);
        assert.equal(Boolean(host.querySelector('.msg-rail-card')), false);
        const dashes = host.querySelectorAll('.msg-rail-dash');
        assert.equal(dashes.length, 2);
        dashes.forEach((el, i) => el.getBoundingClientRect = () => ({ top: 20 + i * 20, left: 0, height: 4, width: 10, bottom: 24 + i * 20, right: 10 }));
        rail.getBoundingClientRect = () => ({ top: 0, left: 0, height: 80, width: 30, bottom: 80, right: 30 });
        const move = new window.Event('mousemove', { bubbles: true });
        Object.defineProperty(move, 'clientY', { value: 22 });
        await act(() => rail.dispatchEvent(move));
        assert.ok(host.querySelector('.msg-rail-card'));
        assert.ok(host.querySelector('strong'));
        assert.equal(Boolean(host.querySelector('[data-audit="rail-user"]')), false);
        assert.ok(host.textContent.includes('data-audit="rail-user"'), 'literal rail-user content is retained');
        assert.equal(Boolean(host.querySelector('[data-audit="rail-assistant"]')), false);
        assert.ok(host.textContent.includes('data-audit="rail-assistant"'), 'literal rail-assistant content is retained');
        const requests = [];
        globalThis.fetch = async (url) => { requests.push(url); return { ok: true, json: async () => url.endsWith('/invoke-trace') ? [] : { name: 'synthetic', source: 'project', path: 'fixture/SKILL.md', description: 'fixture', resources: [], allowed_tools: [], body: '**ordinary skill** <form data-audit="skill-fetch"><input value="synthetic"></form> $<input data-audit="skill-math">$' } }; };
        const { SkillDetailPage } = await import(new URL('components/skills/skill-detail-page.tsx', base));
        await act(async () => { root.render(createElement(SkillDetailPage, { name: 'synthetic' })); });
        assert.ok(requests.includes('/api/skills/synthetic'));
        assert.ok(host.querySelector('strong'));
        assert.equal(Boolean(host.querySelector('[data-audit="skill-fetch"]')), false);
        assert.ok(host.textContent.includes('data-audit="skill-fetch"'), 'literal skill-fetch content is retained');
        assert.equal(Boolean(host.querySelector('[data-audit="skill-math"]')), false);
        assert.ok(host.textContent.includes('data-audit="skill-math"'), 'literal skill-math content is retained');
    }
    finally {
        try {
            if (root)
                await act(() => root.unmount());
        }
        finally {
            host?.remove();
            for (const [name, descriptor] of originals) {
                if (descriptor)
                    Object.defineProperty(globalThis, name, descriptor);
                else
                    delete globalThis[name];
            }
        }
    }
});
