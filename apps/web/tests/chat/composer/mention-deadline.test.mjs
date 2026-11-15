import test from 'node:test';
import assert from 'node:assert/strict';
import { setImmediate as tick } from 'node:timers/promises';
import { expandAtMentions } from '../../../components/chat/composer/attach/mention/at-mention.ts';
test('ordinary lookup expands and failed lookup preserves raw path', async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async (url) => url.includes('good') ? { ok: true, json: async () => ({ path: '/tmp/good.txt', size: 1024 }) } : { ok: false };
    try {
        const r = await expandAtMentions('inspect @good.txt and @missing.txt', null);
        assert.ok(r.text.includes('@ /tmp/good.txt]'));
        assert.ok(r.text.includes('@missing.txt'));
        assert.deepEqual(r.missing, ['missing.txt']);
    }
    finally {
        globalThis.fetch = original;
    }
});
for (const phase of ['fetch', 'body'])
    test(`deadline must abort frozen ${phase}`, async () => {
        const original = globalThis.fetch;
        const originalTimeout = AbortSignal.timeout;
        let controller;
        AbortSignal.timeout = ms => { assert.equal(ms, 15000); controller = new AbortController(); return controller.signal; };
        let suppliedSignal;
        let cleanup;
        let settled = false;
        globalThis.fetch = async (_url, options) => {
            suppliedSignal = options?.signal;
            const pending = () => new Promise((_resolve, reject) => { cleanup = () => reject(new DOMException('aborted', 'AbortError')); suppliedSignal?.addEventListener('abort', cleanup, { once: true }); });
            return phase === 'fetch' ? pending() : { ok: true, json: pending };
        };
        const work = expandAtMentions('inspect @frozen.txt', null).finally(() => settled = true);
        try {
            await tick();
            assert.equal(settled, false);
            assert.ok(suppliedSignal);
            controller.abort();
            await work;
            assert.equal(settled, true);
        }
        finally {
            cleanup();
            const result = await work;
            assert.equal(result.text, 'inspect @frozen.txt');
            assert.deepEqual(result.missing, ['frozen.txt']);
            globalThis.fetch = original;
            AbortSignal.timeout = originalTimeout;
        }
    });
