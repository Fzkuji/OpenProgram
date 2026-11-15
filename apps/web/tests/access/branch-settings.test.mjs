import test from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
registerHooks({resolve(specifier, context, next) {
  if (specifier === './permission-state') return {url: new URL('../../lib/session-store/permission-state.ts', import.meta.url).href, shortCircuit: true};
  return next(specifier, context);
}});
const {branchSettingsPatch, sameSettingsBranch, storedBranchSettings} = await import('../../lib/session-store/branch-settings.ts');
const {branchTarget} = await import('../../lib/agents/branch-target.ts');
const {settingsReadNeeded, settingsShownFor} = await import('../../lib/agents/agent-settings-read.ts');

test('switching same-session branches restores lower permission version and clears missing overrides', () => {
  const patch = branchSettingsPatch({active_branch_id:'a', permission_version:8, thinking:'high', webSearch:false}, 'b', {
    permission_mode:'ask', permission_version:1, web_search:true,
  });
  assert.equal(patch.permission_version, 1);
  assert.equal(patch.permission_mode, 'ask');
  assert.equal(patch.thinking, '');
  assert.equal(patch.webSearch, true);
  assert.equal(patch.fast, false);
  assert.deepEqual(patch.fastByModel, {});
});
test('same-branch loads do not roll permissions backward and late other-branch events are ignored', () => {
  const current = {active_branch_id:'b', permission_version:8};
  const patch = branchSettingsPatch(current, 'b', {permission_mode:'ask', permission_version:1});
  assert.equal(patch.permission_mode, undefined);
  assert.equal(sameSettingsBranch(current, {active_branch_id:'a'}), false);
  assert.equal(sameSettingsBranch(current, {active_branch_id:'b'}), true);
});

test('a reload of the same branch keeps its per-model Fast choices; another branch starts clean', () => {
  const current = {active_branch_id:'a', fastByModel:{'p:m':true}};
  assert.deepEqual(branchSettingsPatch(current, 'a', {service_tier:'priority'}).fastByModel, {'p:m':true});
  assert.deepEqual(branchSettingsPatch(current, 'b', {service_tier:''}).fastByModel, {});
});
test('a settings frame applies to the branch it names, not to the selected one', () => {
  const pane = {active_branch_id:'a'};
  assert.equal(sameSettingsBranch(pane, {branch_id:'a', active_branch_id:'b'}), true);
  assert.equal(sameSettingsBranch(pane, {branch_id:'b', active_branch_id:'a'}), false);
  assert.equal(sameSettingsBranch(pane, {active_branch_id:'a'}), true);
});
test('a branch\'s stored settings read as a composer patch, inherit included', () => {
  const patch = branchSettingsPatch({active_branch_id:'a'}, 'a', storedBranchSettings({
    thinking_effort:null, web_search:false, service_tier:'priority', sandbox_enabled:null,
    permission_state:{mode:'plan', version:3},
  }));
  assert.equal(patch.thinking, '');
  assert.equal(patch.webSearch, false);
  assert.equal(patch.fast, true);
  assert.equal(patch.sandbox, undefined);
  assert.equal(patch.permission_mode, 'plan');
  assert.equal(patch.permission_version, 3);
});
test('a write names the pane\'s branch; a conversation with none guards its first', () => {
  assert.deepEqual(branchTarget('branch_a'), {branch_id:'branch_a'});
  assert.deepEqual(branchTarget(null), {expected_branch_id:null});
});
test('an unfocused pane reads its model again after its branch or Agent moves', () => {
  assert.equal(settingsReadNeeded('s', false, false, 'a|main', undefined), true);
  assert.equal(settingsReadNeeded('s', false, true, 'a|main', 'a|main'), false);
  assert.equal(settingsReadNeeded('s', false, true, 'b|main', 'a|main'), true);
  assert.equal(settingsReadNeeded('s', false, true, 'a|watcher', 'a|main'), true);
  assert.equal(settingsReadNeeded('s', true, true, 'b|main', 'a|main'), false);
});
test('an unfocused pane with an entry reads its model again when the settings epoch moves', () => {
  const before = settingsShownFor('a', 'main', 0);
  assert.equal(settingsReadNeeded('s', false, true, settingsShownFor('a', 'main', 0), before), false);
  assert.equal(settingsReadNeeded('s', false, true, settingsShownFor('a', 'main', 1), before), true);
});
