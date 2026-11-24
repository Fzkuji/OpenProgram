import assert from 'node:assert/strict';
import test from 'node:test';
import { asksAboutComputerUse, computerUseStep, needsForeground, readsWhat, setArgs } from '../../../components/chat/composer/modes/question/approvals/computer-use-step.ts';
import { computerUseRuleApp, parseComputerUseRule } from '../../../lib/computer-use/computer-use-rule.ts';
import { appStepArgs, callStep, callTarget } from '../../../lib/computer-use/computer-use-call.ts';

const zh = (_english, chinese) => chinese;

test('an approval names the one step it covers', () => {
  assert.deepEqual(computerUseStep({ action: 'get_state', app: 'Calculator' }, zh), { verb: '读取窗口内容', target: '' });
  assert.deepEqual(computerUseStep({ action: 'get_state' }, zh, 'screen'), { verb: '读取屏幕内容', target: '' });
  assert.deepEqual(computerUseStep({ action: 'click', element: 7 }, zh), { verb: '点击', target: '元素 #7' });
  assert.deepEqual(computerUseStep({ action: 'wait', seconds: 2 }, zh), { verb: '等待', target: '2 秒' });
  assert.deepEqual(computerUseStep({ action: 'click', x: 10, y: 20, click_count: 2 }, zh), { verb: '双击', target: '(10, 20)' });
  assert.deepEqual(computerUseStep({ action: 'type_text', text: '你好 world' }, zh), { verb: '输入', target: '8 个字' });
  assert.deepEqual(computerUseStep({ action: 'menu', menu_path: ['显示', '科学型'] }, zh), { verb: '选择菜单', target: '显示 › 科学型' });
  assert.deepEqual(computerUseStep({ action: 'batch', actions: [{}, {}, {}, {}] }, zh), { verb: '连续执行', target: '4 步' });
});

test('a read says what it returns, and details drop empty arguments', () => {
  assert.equal(readsWhat({ action: 'get_state' }, zh), '截图 + 界面元素');
  assert.equal(readsWhat({ action: 'get_state', include: 'tree' }, zh), '界面元素');
  assert.equal(readsWhat({ action: 'click' }, zh), '');
  assert.deepEqual(setArgs({ action: 'click', app: 'Notes', x: null, text: '', actions: [], element: 0 }),
    { action: 'click', app: 'Notes', element: 0 });
});

test('only computer_use rules that name an app belong to the apps section', () => {
  assert.equal(computerUseRuleApp('computer_use(window calculator)'), 'calculator');
  assert.equal(computerUseRuleApp(' computer_use(window google chrome) '), 'google chrome');
  assert.equal(computerUseRuleApp('computer_use'), null);
  assert.equal(computerUseRuleApp('bash(git:*)'), null);
  assert.equal(computerUseRuleApp('computer_use(window *)'), null);
  assert.equal(computerUseRuleApp('computer_use(screen)'), null);
  assert.equal(computerUseRuleApp('computer_use(browser chrome)'), null);
  assert.equal(computerUseRuleApp('computer_use(lab screen)'), null);
  assert.equal(computerUseRuleApp('computer_use(notes)'), null);
});

test('every computer_use rule key is read by what it names', () => {
  assert.deepEqual(parseComputerUseRule('computer_use(lab screen)'), { kind: 'device', machine: 'lab', surface: 'screen' });
  assert.deepEqual(parseComputerUseRule('computer_use(browser edge)'), { kind: 'browser', browser: 'edge' });
  assert.deepEqual(parseComputerUseRule('computer_use(browser safari)'), { kind: 'browser', browser: 'safari' });
  assert.deepEqual(parseComputerUseRule('computer_use(browser chrome-beta)'), { kind: 'browser', browser: 'chrome-beta' });
  assert.deepEqual(parseComputerUseRule('computer_use(screen)'), { kind: 'screen' });
  assert.deepEqual(parseComputerUseRule('computer_use(window:*)'), { kind: 'window', app: '*' });
  assert.equal(parseComputerUseRule('computer_use(notes)'), null);
  assert.equal(parseComputerUseRule('bash(git:*)'), null);
});

test('a computer_use window call becomes the one step its approval shows', () => {
  assert.deepEqual(appStepArgs({ type: 'window', app: 'Notes', action: 'click', args: { element: 3 } }),
    { app: 'Notes', element: 3, action: 'click' });
  assert.deepEqual(appStepArgs({ type: 'window', app: 'Notes', actions: [{ action: 'type_text', args: { text: 'a' } }] }),
    { app: 'Notes', action: 'batch', actions: [{ action: 'type_text', text: 'a' }] });
  assert.equal(appStepArgs({ type: 'window', app: 'Notes' }).action, 'get_state');
  assert.deepEqual(appStepArgs({ type: 'window', app: 'Notes', title: 'Inbox' }), { app: 'Notes', window: 'Inbox', action: 'get_state' });
  assert.deepEqual(appStepArgs({ type: 'window', app: 'Notes', title: '42' }), { app: 'Notes', window_id: 42, action: 'get_state' });
  assert.equal(appStepArgs({ type: 'window' }).action, 'list_apps');
  assert.equal(appStepArgs({ type: 'WINDOW', device: 'lab', app: 'Notes' }).action, 'get_state');
  assert.equal(appStepArgs({ type: 'window', app: 'Notes', code: 'pass' }).action, 'code');
  assert.equal(appStepArgs({ type: 'browser', action: 'click' }), null);
  assert.equal(appStepArgs({ type: 'screen', action: 'click' }), null);
  assert.deepEqual(computerUseStep({ action: 'code', code: 'pass' }, zh), { verb: '运行操作它的代码', target: '4 个字符' });
});

test('on Linux, coordinate input and key presses are the steps that bring the window forward', () => {
  assert.equal(needsForeground({ action: 'click', element: 3 }), false);
  assert.equal(needsForeground({ action: 'click', element: 0 }), false);
  assert.equal(needsForeground({ action: 'click', x: 10, y: 20 }), true);
  assert.equal(needsForeground({ action: 'scroll', element: 4, direction: 'down' }), false);
  assert.equal(needsForeground({ action: 'drag', x: 1, y: 2, to_x: 3, to_y: 4 }), true);
  assert.equal(needsForeground({ action: 'hover', element: 2 }), true);
  assert.equal(needsForeground({ action: 'press_key', key: 'Return' }), false);
  assert.equal(needsForeground({ action: 'press_key', key: 'ctrl+a' }), true);
  // Windows posts plain input to the window; only chords and paste come forward.
  assert.equal(needsForeground({ action: 'click', x: 10, y: 20 }, 'windows'), false);
  assert.equal(needsForeground({ action: 'press_key', key: 'Escape' }, 'windows'), false);
  assert.equal(needsForeground({ action: 'press_key', key: '+' }, 'windows'), false);
  assert.equal(needsForeground({ action: 'press_key', key: 'ctrl++' }, 'windows'), true);
  assert.equal(needsForeground({ action: 'batch', actions: [{ action: 'paste', text: 'x' }] }, 'windows'), true);
  assert.equal(needsForeground({ action: 'click', x: 10, y: 20 }, 'darwin'), false);
  assert.equal(needsForeground({ action: 'type_text', text: 'hi' }), false);
  assert.equal(needsForeground({ action: 'batch', actions: [{ action: 'set_value' }, { action: 'click', x: 1, y: 1 }] }), true);
  assert.equal(needsForeground({ action: 'batch', actions: [{ action: 'set_value' }, null] }), false);
});

test('an action written in another tool\'s names becomes the step in the present names', () => {
  assert.deepEqual(appStepArgs({ type: 'window', app: 'Notes', action: 'get_app_state' }), { app: 'Notes', action: 'get_state' });
  assert.deepEqual(appStepArgs({ type: 'window', app: 'Notes', action: 'double_click', args: { element_index: 3 } }),
    { app: 'Notes', element: 3, click_count: 2, action: 'click' });
  assert.deepEqual(appStepArgs({ type: 'window', app: 'Notes', action: 'sleep', args: { duration: 2 } }),
    { app: 'Notes', seconds: 2, action: 'wait' });
  assert.deepEqual(appStepArgs({ type: 'window', app: 'Notes', actions: [{ action: 'left_click', args: { coordinate: [4, 5] } }] }).actions,
    [{ action: 'click', x: 4, y: 5 }]);
  assert.deepEqual(callTarget({ type: 'browser', app: 'Google Chrome' }),
    { target: 'browser chrome', family: 'browser', browser: 'chrome', machine: '' });
  assert.deepEqual(callTarget({ type: 'browser', app: 'Mozilla Firefox' }),
    { target: 'browser firefox', family: 'browser', browser: 'firefox', machine: '' });
  assert.equal(callTarget({ type: 'browser', app: 'netscape' }).browser, '');
  assert.deepEqual(callTarget({ type: 'screen', device: 'this computer' }),
    { target: 'screen', family: 'screen', browser: '', machine: '' });
  assert.equal(needsForeground(appStepArgs({ type: 'window', app: 'Notes', action: 'click', args: { element_index: 3 } })), false);
});

test('the computer-use card asks about an application, the whole screen or the user\'s own browser', () => {
  const asks = (call) => asksAboutComputerUse(callTarget(call), callStep(call));
  assert.equal(asks({ type: 'window', app: 'Notes', action: 'click', args: { element: 1 } }), true);
  assert.equal(asks({ type: 'window', action: 'list_apps' }), false);
  // A batch that names its application only in its steps asks about that application, as the worker grants it.
  const batch = { type: 'window', actions: [{ action: 'click', args: { app: 'Terminal', element: 1 } }] };
  assert.equal(asks(batch), true);
  assert.equal(callStep(batch).app, 'Terminal');
  assert.equal(callStep({ type: 'window', app: 'Notes', actions: [{ action: 'click', args: { app: 'Terminal' } }] }).app, 'Notes');
  assert.equal(callStep({ type: 'screen', actions: [{ action: 'click', args: { app: 'Terminal' } }] }).app, undefined);
  assert.equal(asks({ type: 'screen', action: 'click', args: { x: 1, y: 2 } }), true);
  assert.equal(asks({ type: 'browser', app: 'chrome', action: 'click', args: { element: 'e3' } }), true);
  assert.equal(asks({ type: 'browser', app: 'edge', action: 'list_tabs' }), true);
  // The built-in browser asks the generic card; a device that was set up asks nothing.
  assert.equal(asks({ type: 'browser', action: 'upload_file', args: { path: '/tmp/a' } }), false);
  assert.equal(asks({ type: 'window', device: 'lab', app: 'Notes', action: 'click', args: { element: 1 } }), false);
  assert.equal(asks({ type: 'screen', device: 'lab', action: 'click', args: { x: 1, y: 2 } }), false);
});
