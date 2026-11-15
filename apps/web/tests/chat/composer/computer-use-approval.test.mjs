import assert from 'node:assert/strict';
import test from 'node:test';
import { computerUseStep, needsForeground, readsWhat, setArgs } from '../../../components/chat/composer/modes/question/approvals/computer-use-step.ts';
import { computerUseRuleApp } from '../../../lib/computer-use/computer-use-rule.ts';
import { appStepArgs } from '../../../lib/computer-use/computer-use-agent-call.ts';

const zh = (_english, chinese) => chinese;

test('an approval names the one step it covers', () => {
  assert.deepEqual(computerUseStep({ action: 'get_app_state', app: 'Calculator' }, zh), { verb: '读取窗口内容', target: '' });
  assert.deepEqual(computerUseStep({ action: 'click', element_index: 7 }, zh), { verb: '点击', target: '元素 #7' });
  assert.deepEqual(computerUseStep({ action: 'click', x: 10, y: 20, click_count: 2 }, zh), { verb: '双击', target: '(10, 20)' });
  assert.deepEqual(computerUseStep({ action: 'type_text', text: '你好 world' }, zh), { verb: '输入', target: '8 个字' });
  assert.deepEqual(computerUseStep({ action: 'menu', menu_path: ['显示', '科学型'] }, zh), { verb: '选择菜单', target: '显示 › 科学型' });
  assert.deepEqual(computerUseStep({ action: 'batch', actions: [{}, {}, {}, {}] }, zh), { verb: '连续执行', target: '4 步' });
});

test('a read says what it returns, and details drop empty arguments', () => {
  assert.equal(readsWhat({ action: 'get_app_state' }, zh), '截图 + 界面元素');
  assert.equal(readsWhat({ action: 'get_app_state', include: 'tree' }, zh), '界面元素');
  assert.equal(readsWhat({ action: 'click' }, zh), '');
  assert.deepEqual(setArgs({ action: 'click', app: 'Notes', x: null, text: '', actions: [], element_index: 0 }),
    { action: 'click', app: 'Notes', element_index: 0 });
});

test('only computer_use rules that name an app belong to the apps section', () => {
  assert.equal(computerUseRuleApp('computer_use(calculator)'), 'calculator');
  assert.equal(computerUseRuleApp(' computer_use( google chrome ) '), 'google chrome');
  assert.equal(computerUseRuleApp('computer_use'), null);
  assert.equal(computerUseRuleApp('bash(git:*)'), null);
  assert.equal(computerUseRuleApp('computer_use_agent(notes)'), 'notes');
  assert.equal(computerUseRuleApp('computer_use_agent(vm:http://10.0.0.2:5000)'), null);
});

test('a computer_use_agent app call becomes the one step its approval shows', () => {
  assert.deepEqual(appStepArgs({ target: 'app', action: 'click', args: { app: 'Notes', element_index: 3 } }),
    { app: 'Notes', element_index: 3, action: 'click' });
  assert.deepEqual(appStepArgs({ target: 'app', args: { app: 'Notes' }, actions: [{ action: 'type_text', args: { text: 'a' } }] }),
    { app: 'Notes', action: 'batch', actions: [{ action: 'type_text', text: 'a' }] });
  assert.equal(appStepArgs({ target: 'app', args: { app: 'Notes' } }).action, 'get_app_state');
  assert.equal(appStepArgs({ target: 'app', args: { app: 'Notes' }, code: 'pass' }).action, 'code');
  assert.equal(appStepArgs({ target: 'browser', action: 'click' }), null);
  assert.deepEqual(computerUseStep({ action: 'code', code: 'pass' }, zh), { verb: '运行操作它的代码', target: '4 个字符' });
});

test('on Linux, coordinate input and key presses are the steps that bring the window forward', () => {
  assert.equal(needsForeground({ action: 'click', element_index: 3 }), false);
  assert.equal(needsForeground({ action: 'click', element_index: 0 }), false);
  assert.equal(needsForeground({ action: 'click', x: 10, y: 20 }), true);
  assert.equal(needsForeground({ action: 'scroll', element_index: 4, direction: 'down' }), false);
  assert.equal(needsForeground({ action: 'drag', x: 1, y: 2, to_x: 3, to_y: 4 }), true);
  assert.equal(needsForeground({ action: 'hover', element_index: 2 }), true);
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
