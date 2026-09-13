import assert from 'node:assert/strict';
import test from 'node:test';
import { systemAccessRequired, rememberSystemAccessRequest, rememberSystemAccessFromTree, hasSystemAccessRequest, consumeSystemAccessRequest, systemAccessRequestId } from '../../lib/access/system-access-result.ts';
test('only structured blocked results expose known system capabilities', () => {
 const value={status:'infeasible',reason_code:'system_access_required',system_access:[{id:'screen_recording',status:'not_granted'},{id:'accessibility',status:'granted'},{id:'arbitrary',status:'not_granted'}]};
 assert.deepEqual(systemAccessRequired(JSON.stringify(value)),['screen_recording']);
 assert.deepEqual(systemAccessRequired({...value,status:'succeeded'}),[]);
 assert.deepEqual(systemAccessRequired({status:'infeasible',summary:'please open settings'}),[]);
 assert.deepEqual(systemAccessRequired('not json'),[]);
});

test('live completion marker survives rendering replacement but is consumed once', () => {
 const output={status:'infeasible',reason_code:'system_access_required',system_access:[{id:'screen_recording',status:'not_granted'}]};
 assert.equal(hasSystemAccessRequest('session','id'),false);
 rememberSystemAccessRequest('session','id',output);
 assert.equal(hasSystemAccessRequest('session','id'),true);
 assert.equal(hasSystemAccessRequest('other','id'),false);
 consumeSystemAccessRequest('session','id');
 rememberSystemAccessRequest('session','id',output);
 assert.equal(hasSystemAccessRequest('session','id'),false);
});

test('tree marker keys on persisted path, not the live envelope id', () => {
 const output={status:'infeasible',reason_code:'system_access_required',system_access:[{id:'screen_recording',status:'not_granted'}]};
 rememberSystemAccessFromTree('tree_sess','asst_id',{path:'code_node',name:'gui_agent',output}, 'gui_agent');
 assert.equal(hasSystemAccessRequest('tree_sess','code_node'),true);
 assert.equal(hasSystemAccessRequest('tree_sess','asst_id'),false);
 rememberSystemAccessFromTree('tree_sess','asst_id',{path:'code_node',name:'gui_agent',output}, 'gui_agent');
 consumeSystemAccessRequest('tree_sess','code_node');
 rememberSystemAccessFromTree('tree_sess','asst_id',{path:'code_node',name:'gui_agent',output}, 'gui_agent');
 assert.equal(hasSystemAccessRequest('tree_sess','code_node'),false);
});

test('consumer looks up tree.path then row id', () => {
 const tree={path:'result_node'};
 assert.equal(systemAccessRequestId(tree,'result_envelope'),'result_node');
 assert.equal(systemAccessRequestId({},'result_envelope'),'result_envelope');
 assert.equal(systemAccessRequestId(undefined,'result_envelope'),'result_envelope');
 const output={status:'infeasible',reason_code:'system_access_required',system_access:[{id:'screen_recording',status:'not_granted'}]};
 rememberSystemAccessFromTree('consume_sess','result_envelope',{path:'result_node',name:'gui_agent',output},'gui_agent');
 const accessId=systemAccessRequestId({path:'result_node'},'result_envelope');
 assert.equal(hasSystemAccessRequest('consume_sess',accessId),true);
 assert.equal(hasSystemAccessRequest('consume_sess','result_envelope'),false);
 consumeSystemAccessRequest('consume_sess',accessId);
 assert.equal(hasSystemAccessRequest('consume_sess',accessId),false);
});
