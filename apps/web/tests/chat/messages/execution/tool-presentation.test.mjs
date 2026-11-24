import assert from 'node:assert/strict';
import test from 'node:test';
import { presentTool, summarizeResult } from '../../../../components/chat/messages/execution/tool-presentation.ts';
import { rememberMachineNames } from '../../../../lib/computer-use/computer-use-call.ts';

const en = (english) => english;

test('commands read as a verb with the first command line', () => {
  const view = presentTool('bash', JSON.stringify({ command: 'npm test\necho done' }), en);
  assert.equal(view.tone, 'shell');
  assert.equal(view.title, 'Ran');
  assert.equal(view.target, 'npm test');
});

test('file tools shorten long paths to their last segments', () => {
  const view = presentTool('edit', JSON.stringify({ path: '/Users/me/project/src/app/page.tsx' }), en);
  assert.equal(view.tone, 'edit');
  assert.equal(view.target, '…/src/app/page.tsx');
});

test('browser tools combine command and url; unknown tools keep their name', () => {
  assert.equal(presentTool('web_use', JSON.stringify({ command: 'observe' }), en).target, 'observe');
  const unknown = presentTool('custom_tool', JSON.stringify({ query: 'abc' }), en);
  assert.equal(unknown.tone, 'function');
  assert.equal(unknown.title, 'custom_tool');
  assert.equal(unknown.target, 'abc');
});

test('raw input that is not JSON still yields a target', () => {
  assert.equal(presentTool('bash', 'ls -la', en).target, 'ls -la');
});

test('every tool glyph is a vendored Solar Bold Duotone icon', async () => {
  const { SOLAR_BODIES } = await import('../../../../components/solar-icons/bodies.ts');
  assert.equal(presentTool('bash', '{}', en).icon, 'programming');
  assert.equal(presentTool('custom_tool', '{}', en).icon, 'sledgehammer');
  const names = ['bash', 'execute_code', 'edit', 'read', 'list', 'glob', 'grep', 'lsp_definition',
    'web_search', 'web_use', 'image_generate', 'image_analyze', 'send_file', 'agent', 'read_conversation',
    'ask_user_question', 'enter_plan_mode', 'cron', 'program', 'skill', 'resource', 'todo_write',
    'memory_search', 'worktree_create', 'self_update', 'mcp_call', 'custom_tool'];
  for (const name of names) {
    const { icon } = presentTool(name, '{}', en);
    assert.ok(icon in SOLAR_BODIES, `${name} -> ${icon} must be in bodies.ts`);
  }
});

test('JSON results are summarised instead of printed raw', () => {
  assert.equal(summarizeResult('{"ok": true, "title": "Google", "url": "https://google.com"}', en), 'Google · https://google.com');
  assert.equal(summarizeResult('{"ok": false, "error": "timeout"}', en), 'timeout');
  assert.equal(summarizeResult('{"progress": "Building the new version…", "phase": "building"}', en), 'Building the new version…');
  assert.equal(summarizeResult('[1, 2, 3]', en), '3 items');
  assert.equal(summarizeResult('{"ok": true, "pages": [1, 2]}', en), '2 items');
  assert.equal(summarizeResult('plain\nsecond line', en), 'plain');
  assert.equal(summarizeResult('<guide name="self_update">\nHow it works.\n</guide>\n\n{"phase": "preparing", "status": "ok"}', en), 'ok');
  assert.equal(summarizeResult('<guide name="a">x</guide>\n\n<guide name="b">y</guide>\n\nNotes (running)', en), 'Notes (running)');
  assert.equal(summarizeResult('{"frame_id": "f1", "title": "Google", "url": "https://www.google.com/webhp', en), 'Google');
  assert.equal(summarizeResult('{"frame_id": "f1", "nodes": [1, 2', en), 'structured output');
});

test('a computer_use window call reads as the action in the app', () => {
  const row = (call) => presentTool('computer_use', JSON.stringify({ type: 'window', ...call }), en);
  const click = row({ app: 'Calculator', action: 'click', args: { element_index: 20 } });
  assert.equal(click.title, 'Clicked in Calculator');
  assert.equal(click.icon, 'monitor');
  assert.equal(row({ app: 'Notes', action: 'press_key', args: { key: 'super+c' } }).target, 'super+c');
  assert.equal(row({ action: 'list_apps' }).title, 'Listed apps');
  const menu = row({ app: 'Notes', action: 'menu', args: { menu_path: ['File', 'Export as PDF…'] } });
  assert.equal(menu.title, 'Used the menu in Notes');
  assert.equal(menu.target, 'File › Export as PDF…');
  assert.equal(row({ app: 'Music', action: 'start_audio_recording' }).title, 'Started recording in Music');
  // The GUI Agent's capabilities keep their own label.
  assert.equal(presentTool('browser_use', JSON.stringify({ task: 'inspect' }), en).title, 'Browser Use');
});

test('web_use shows the nested act action rather than the command', () => {
  assert.equal(presentTool('web_use', JSON.stringify({ command: 'act', arguments: { action: 'drag', ref: 'e1', to_ref: 'e2' } }), en).target, 'drag');
  assert.equal(presentTool('web_use', JSON.stringify({ command: 'observe', arguments: { url: 'https://example.com' } }), en).target, 'observe https://example.com');
});

test('computer_use reads like the screen it operates', () => {
  const click = presentTool('computer_use', JSON.stringify({ type: 'window', app: 'Calculator', action: 'click', args: { element_index: 20 } }), en);
  assert.equal(click.title, 'Clicked in Calculator');
  const steps = presentTool('computer_use', JSON.stringify({ type: 'window', app: 'Notes',
    actions: [{ action: 'click', args: { element_index: 1 } }, { action: 'type_text', args: { text: 'hi' } }] }), en);
  assert.equal(steps.title, 'Ran steps in Notes');
  assert.equal(steps.target, '2 steps');
  assert.equal(presentTool('computer_use', JSON.stringify({ type: 'window', app: 'Notes' }), en).title, 'Read in Notes');
  const code = presentTool('computer_use', JSON.stringify({ type: 'window', app: 'Numbers', code: 'for v in x:\n  pass' }), en);
  assert.deepEqual([code.title, code.target, code.tone], ['Ran code in Numbers', 'for v in x:', 'shell']);
  const page = presentTool('computer_use', JSON.stringify({ type: 'browser', action: 'navigate', args: { url: 'https://example.com' } }), en);
  assert.deepEqual([page.title, page.target, page.tone], ['Browser', 'navigate https://example.com', 'web']);
});

test('computer_use names every target: the screen, the user\'s browsers and other devices', () => {
  const zh = (_english, chinese) => chinese;
  const row = (call, text = en) => {
    const view = presentTool('computer_use', JSON.stringify(call), text);
    return [view.title, view.target];
  };
  rememberMachineNames([{ id: 'lab', name: 'Lab VM' }]);
  assert.deepEqual(row({ type: 'screen', action: 'click', args: { x: 1, y: 2 } }), ['Clicked on the screen', undefined]);
  assert.deepEqual(row({ type: 'screen', action: 'press_key', args: { key: 'super+space' } }, zh), ['在屏幕上按键', 'super+space']);
  assert.deepEqual(row({ type: 'screen', action: 'list_displays' }), ['Listed displays', undefined]);
  assert.deepEqual(row({ type: 'screen', action: 'zoom', args: { x: 0, y: 0, width: 10, height: 10 } }), ['Zoomed on the screen', undefined]);
  assert.deepEqual(row({ type: 'Screen', device: 'lab', action: 'hover', args: { x: 1, y: 2 } }), ["Hovered on Lab VM's screen", undefined]);
  assert.deepEqual(row({ type: 'screen', device: 'lab', action: 'type_text', args: { text: 'hi' } }, zh), ['在 Lab VM 的屏幕上输入', 'hi']);
  assert.deepEqual(row({ type: 'screen', device: 'other', action: 'get_state' }), ["Read on other's screen", undefined]);
  assert.deepEqual(row({ type: 'window', device: 'lab', app: 'Notes', action: 'set_value', args: { element: 4, value: 'x' } }),
    ['Set value in Notes on Lab VM', 'x']);
  assert.deepEqual(row({ type: 'window', app: 'Notes', action: 'find', args: { text: 'Save' } }), ['Searched in Notes', 'Save']);
  assert.deepEqual(row({ type: 'window', app: 'Notes', title: 'Inbox', action: 'get_state' }), ['Read in Notes', 'Inbox']);
  assert.deepEqual(row({ type: 'browser', app: 'chrome', action: 'create_tab', args: { url: 'https://example.com' } }), ['Chrome', 'create_tab https://example.com']);
  assert.deepEqual(row({ type: 'browser', app: 'Microsoft Edge', action: 'list_tabs' }), ['Edge', 'list_tabs']);
  assert.deepEqual(row({ type: 'browser', app: 'firefox', action: 'list_tabs' }), ['Firefox', 'list_tabs']);
  assert.deepEqual(row({ type: 'browser', app: 'chrome_canary', action: 'list_tabs' }), ['Chrome Canary', 'list_tabs']);
  assert.deepEqual(row({ type: 'browser', device: 'lab', action: 'navigate', args: { url: 'http://localhost:3000' } }, zh),
    ['Lab VM 上的浏览器', 'navigate http://localhost:3000']);
  assert.deepEqual(row({ type: 'screen', device: 'lab', code: 'await cu.get_state()' }), ["Ran code on Lab VM's screen", 'await cu.get_state()']);
  rememberMachineNames([]);
});

test('an action written in another tool\'s names reads in the present wording', () => {
  const row = (call) => {
    const view = presentTool('computer_use', JSON.stringify(call), en);
    return [view.title, view.target];
  };
  assert.deepEqual(row({ type: 'window', app: 'Notes', action: 'get_app_state' }), ['Read in Notes', undefined]);
  assert.deepEqual(row({ type: 'window', app: 'Notes', action: 'double_click', args: { element_index: 3 } }), ['Clicked in Notes', undefined]);
  assert.deepEqual(row({ type: 'window', app: 'Notes', action: 'hotkey', args: { keys: ['cmd', 'c'] } }), ['Pressed key in Notes', 'cmd+c']);
  assert.deepEqual(row({ type: 'window', app: 'Notes', action: 'fill', args: { element_index: 3, text: 'abc' } }), ['Set value in Notes', 'abc']);
  assert.deepEqual(row({ type: 'browser', app: 'chrome', action: 'open', args: { url: 'https://example.com' } }),
    ['Chrome', 'create_tab https://example.com']);
  assert.deepEqual(row({ type: 'browser', action: 'observe' }), ['Browser', 'get_state']);
});

test('a result header names the target and is never the result', () => {
  assert.equal(summarizeResult('[device="lab", type="screen"]\nDone: click. Coordinates are pixels of this screenshot.', en),
    'Done: click. Coordinates are pixels of this screenshot.');
  assert.equal(summarizeResult('[type="window", this computer]\n{"ok": true, "title": "Notes"}', en), 'Notes');
  assert.equal(summarizeResult('[device="lab", type="screen"]\n<guide name="computer_use.screen">x</guide>\n\nScreenshot of the machine.', en),
    'Screenshot of the machine.');
  assert.equal(summarizeResult('<guide name="computer_use.window">x</guide>\n[device="lab", type="window"]\nNotes (running)', en), 'Notes (running)');
  assert.equal(summarizeResult('[type="browser", app="chrome", this computer]\nTabs of Google Chrome', en), 'Tabs of Google Chrome');
  assert.equal(summarizeResult('[device="lab", type="screen"]\nUNKNOWN_ACTION: lab screen has no action \'tap\'.', en),
    "UNKNOWN_ACTION: lab screen has no action 'tap'.");
  // A JSON array is still a result.
  assert.equal(summarizeResult('["a", "b"]', en), '2 items');
});
