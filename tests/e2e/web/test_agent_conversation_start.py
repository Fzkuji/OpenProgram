"""Real Agent draft/start/send/ACK stores in a browser, with isolated transports."""
from pathlib import Path
import asyncio
import json
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser

_ENTRY = r'''
import {startAgentConversation} from './lib/agents/start-conversation';
import {useSessionStore} from './lib/session-store';
import {useCenterTabs} from './lib/tabs/center-tabs-store';
import {sendChatMessage} from './components/chat/composer/submit/send-chat-message';
import {applyChatWsMessage} from './lib/net/ws/chat-stream';
import {setSocket} from './lib/runtime-bridge/state';
import {setNavigate} from './lib/nav/navigate';
import {readSessionDraftState} from './lib/chat/send/session-draft-persistence';
const makeAgent=(id,model,effort)=>({id,name:id+' agent',description:'Saved',default:false,
  model:{provider:'test',id:model},thinking_effort:effort,system_prompt:'saved '+id,
  tools:{mode:'automatic'},skills:{allowed:[],disabled:[],categories:[]},
  mcp:{allowed:[],disabled:[],required:[]},
  memory:{mode:'read_write',read_spaces:['self'],write_space:'self',required:false},
  identity:{name:'',mention_patterns:[]},session_scope:'per-account-channel-peer',
  session_idle_minutes:0,session_daily_reset:''});
window.savedAgents={alpha:makeAgent('alpha','reasoning','high'),beta:makeAgent('beta','fast','')};
window.httpCalls=[];window.wsFrames=[];window.navigations=[];window.unexpectedRequests=[];
window.callbackCounts={sent:0,ack:0,reject:0};
window.fetch=async(input,options={})=>{
  const url=String(input),method=options.method||'GET';window.httpCalls.push({url,method});
  const reply=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
  if(method!=='GET'){window.unexpectedRequests.push({url,method});return reply({error:'Unexpected write'},500)}
  const match=url.match(/^\/api\/agents\/([^/?]+)$/);
  if(match)return window.savedAgents[match[1]]?reply({agent:window.savedAgents[match[1]]}):reply({error:'Agent missing'},404);
  if(url==='/api/providers/test/models')return reply({models:[
    {id:'reasoning',thinking_levels:['low','high'],default_thinking_level:'low'},
    {id:'fast',thinking_levels:[],default_thinking_level:''},
  ]});
  if(url==='/api/providers')return reply([]);
  if(url==='/api/providers/list')return reply({providers:[{id:'test',label:'Fixture provider',enabled:true,configured:true}]});
  if(url.startsWith('/api/agent_settings'))return reply({chat:{provider:'test',model:'reasoning'}});
  window.unexpectedRequests.push({url,method});return reply({error:'Unexpected fixture request'},500);
};
setNavigate(path=>window.navigations.push(path));
setSocket({readyState:WebSocket.OPEN,send:raw=>window.wsFrames.push(JSON.parse(raw))});
window.app={
  async start(options){await startAgentConversation(options);return useSessionStore.getState().activeChatKey},
  send(key,text='hello',background=false){
    const settings=useSessionStore.getState().composerSettingsBySession[key];
    return sendChatMessage({text,sessionId:key,thinking:settings.thinking,webSearchEnabled:settings.webSearch,background,
      onSent:()=>window.callbackCounts.sent++,onAck:()=>window.callbackCounts.ack++,onReject:()=>window.callbackCounts.reject++});
  },
  frame:applyChatWsMessage,
  state(){const s=useSessionStore.getState();return {settings:s.composerSettingsBySession,drafts:s.composerDrafts,
    activeChatKey:s.activeChatKey,currentSessionId:s.currentSessionId,tabs:useCenterTabs.getState().tabs}},
  persisted:readSessionDraftState,
  draft(key,text){useSessionStore.getState().setComposerInputFor(key,text)},
  drop(key){useSessionStore.getState().dropChatDraft(key)},
};
'''

_ENTRY += r'''
import React from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {SessionScopeProvider} from './lib/session-store/session-scope';
import {AgentSelector} from './components/chat/top-bar/menus/agent-selector';
import {DropdownMenu,DropdownMenuContent,DropdownMenuTrigger} from './components/ui/dropdown-menu';
import {useThinkingEffort} from './components/chat/composer/controls/effort/use-thinking-effort';
window.modelPicksFinished=0;window.delayedModels=[];window.modelWrites=[];window.delayModels=false;
const baseFetch=window.fetch;
window.fetch=async(input,options={})=>{
 const url=String(input);
 const reply=v=>new Response(JSON.stringify(v),{headers:{'Content-Type':'application/json'}});
 if(url==='/api/models/enabled')return reply({models:[{provider:'test',id:'reasoning',name:'Reasoning',enabled:true},{provider:'test',id:'fast',name:'Fast',enabled:true}]});
 if(url==='/api/model'){window.modelWrites.push(JSON.parse(options.body));return reply({ok:true});}
 if(url==='/api/providers/test/models'&&window.delayModels)return await new Promise(resolve=>window.delayedModels.push(()=>baseFetch(input,options).then(resolve)));
 return baseFetch(input,options);
};
function Hook(){const effort=useThinkingEffort();window.effort=effort;return React.createElement('div',{'data-testid':'effort'},effort.thinking);}
window.mount=key=>{const el=document.createElement('div');document.body.appendChild(el);const root=createRoot(el);// The selector renders the rows of the top bar's open <DropdownMenu>; mount it inside one, held open.
const menu=React.createElement(DropdownMenu,{open:true,modal:false},React.createElement(DropdownMenuTrigger,{asChild:true},React.createElement('button',{type:'button'},'models')),React.createElement(DropdownMenuContent,null,React.createElement(AgentSelector,{onClose:()=>window.modelPicksFinished++})));
root.render(React.createElement(QueryClientProvider,{client:new QueryClient()},React.createElement(SessionScopeProvider,{sid:key},menu,React.createElement(Hook))));};
'''

_BUILD = r'''
const path=require('node:path'),esbuild=require('esbuild');
esbuild.buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'ts'},
  bundle:true,format:'iife',platform:'browser',jsx:'automatic',
  outfile:process.argv[2],tsconfig:path.join(process.argv[1],'tsconfig.json')});
'''


@pytest.fixture(scope='module')
def conversation_bundle(tmp_path_factory):
    bundle = tmp_path_factory.mktemp('agent-conversation-start') / 'fixture.js'
    subprocess.run(['node', '-e', _BUILD, str(ROOT / 'apps/web'), str(bundle), _ENTRY],
                   cwd=ROOT, check=True, capture_output=True, text=True)
    return bundle.read_text()


@pytest.fixture
def conversation_browser(conversation_bundle, rejected_agent_frame):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        errors, network = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def route(request):
            url = request.request.url
            if url == 'https://agent-conversation.test/':
                request.fulfill(content_type='text/html', body='<!doctype html><html lang="en"><meta charset="utf-8"><body><script src="/fixture.js"></script></body></html>')
            elif url == 'https://agent-conversation.test/fixture.js':
                request.fulfill(content_type='application/javascript', body=conversation_bundle)
            else:
                network.append(url)
                request.abort()
        page.route('**/*', route)
        page.goto('https://agent-conversation.test/')
        page.wait_for_function('window.app !== undefined')
        yield page
        try:
            assert not errors, errors
            assert not network, network
            assert not page.evaluate('window.unexpectedRequests'), page.evaluate('window.unexpectedRequests')
        finally:
            browser.close()


def _two_drafts(page):
    return page.evaluate('''async()=>{
      const first=await app.start({agentId:'alpha'});
      const config=structuredClone(savedAgents.beta);delete config.id;
      config.system_prompt='UNSAVED_BETA';config.tools={mode:'none'};
      const second=await app.start({agentId:'beta',trial:true,config});
      config.system_prompt='MUTATED_AFTER_START';
      return {first,second};
    }''')


def test_agent_drafts_are_independent_and_background_send_uses_own_intent(conversation_browser):
    page = conversation_browser
    keys = _two_drafts(page)
    first, second = keys['first'], keys['second']
    assert first != second and first.startswith('local_') and second.startswith('local_')
    assert not page.evaluate("httpCalls.filter(call=>call.url.includes('session_id=local_'))"), 'Unsent Agent drafts must not hydrate server sessions'
    state = page.evaluate('app.state()')
    assert state['activeChatKey'] == second
    assert state['currentSessionId'] is None
    assert state['settings'][first]['thinking'] == 'high'
    assert state['settings'][second]['agentInvocation']['config']['system_prompt'] == 'UNSAVED_BETA'
    assert state['settings'][second]['agentInvocation']['config']['memory']['mode'] == 'read_only'
    assert page.evaluate('(key)=>app.send(key,"alpha background",true)', first)
    frame = page.evaluate('wsFrames.at(-1)')
    assert frame['session_id'] == first
    assert frame['agent_id'] == 'alpha'
    assert 'agent_config' not in frame and 'agent_trial' not in frame
    assert page.evaluate('app.state().activeChatKey') == second
    # Socket.write is not admission. Both intentions remain stored durably.
    persisted = page.evaluate('app.persisted().composerSettingsBySession')
    assert persisted[first]['agentInvocation']['agentId'] == 'alpha'
    assert persisted[second]['agentInvocation']['agentId'] == 'beta'
    assert page.evaluate('callbackCounts') == {'sent': 1, 'ack': 0, 'reject': 0}


def test_ack_consumes_only_matching_agent_intent(conversation_browser):
    page = conversation_browser
    keys = _two_drafts(page)
    page.evaluate('keys=>{app.send(keys.first,"first",true);app.send(keys.second,"second")}', keys)
    frames = page.evaluate('wsFrames')
    assert frames[-1]['agent_config']['system_prompt'] == 'UNSAVED_BETA'
    assert frames[-1]['agent_trial'] is True
    page.evaluate('sid=>app.frame({type:"chat_ack",data:{session_id:sid,msg_id:"u-alpha",text:"first"}})', keys['first'])
    state = page.evaluate('app.state()')
    assert state['settings'][keys['first']].get('agentInvocation') is None
    assert state['settings'][keys['second']]['agentInvocation']['agentId'] == 'beta'
    assert state['activeChatKey'] == keys['second']
    assert page.evaluate('callbackCounts.ack') == 1
    persisted = page.evaluate('app.persisted().composerSettingsBySession')
    assert 'agentInvocation' not in persisted[keys['first']]
    assert persisted[keys['second']]['agentInvocation']['trial'] is True
    page.evaluate('sid=>app.frame({type:"chat_ack",data:{session_id:sid,msg_id:"u-beta",text:"second"}})', keys['second'])
    assert 'agentInvocation' not in page.evaluate('app.persisted().composerSettingsBySession')[keys['second']]


@pytest.fixture
def rejected_agent_frame():
    from openprogram.interfaces.server.ws_actions.chat import handle_chat
    class Socket:
        frames = []
        async def send_text(self, value):
            self.frames.append(json.loads(value))
    socket = Socket()
    asyncio.run(handle_chat(socket, {'session_id': 'local_browser_rejection', 'text': 'hello',
                                    'agent_id': '../invalid'}))
    return socket.frames[0]


def test_agent_rejection_preserves_intent_and_releases_retry(conversation_browser, rejected_agent_frame):
    page = conversation_browser
    key = page.evaluate('app.start({agentId:"alpha"})')
    assert page.evaluate('key=>app.send(key)', key)
    # The transport's pre-admission error owns the provisional session ID;
    # no successful user message exists yet.
    rejected_agent_frame['data']['session_id'] = key
    page.evaluate('frame=>app.frame(frame)', rejected_agent_frame)
    assert page.evaluate('app.persisted().composerSettingsBySession')[key]['agentInvocation']['agentId'] == 'alpha'
    assert page.evaluate('callbackCounts.reject') == 1
    assert page.evaluate('key=>app.send(key,"retry")', key)
    assert len(page.evaluate('wsFrames')) == 2
    assert page.evaluate('wsFrames.at(-1).agent_id') == 'alpha'


def test_agent_intents_survive_refresh_and_drop_removes_persisted_configuration(conversation_browser):
    page = conversation_browser
    keys = _two_drafts(page)
    page.evaluate('keys=>{app.draft(keys.first,"Alpha unsent");app.draft(keys.second,"Beta unsent")}', keys)
    page.reload()
    page.wait_for_function('window.app !== undefined')
    state = page.evaluate('app.state()')
    assert state['settings'][keys['first']]['agentInvocation']['agentId'] == 'alpha'
    assert state['settings'][keys['second']]['agentInvocation']['config']['system_prompt'] == 'UNSAVED_BETA'
    assert state['drafts'][keys['first']] == 'Alpha unsent'
    page.evaluate('key=>app.drop(key)', keys['second'])
    persisted = page.evaluate('app.persisted()')
    assert keys['second'] not in persisted['composerSettingsBySession']
    assert keys['second'] not in persisted['composerDrafts']
    assert keys['first'] in persisted['composerSettingsBySession']
    page.reload()
    page.wait_for_function('window.app !== undefined')
    assert keys['second'] not in page.evaluate('app.state().settings')


def test_saved_memory_off_limits_trial_and_missing_agent_creates_no_draft(conversation_browser):
    page = conversation_browser
    key = page.evaluate('''async()=>{
      savedAgents.beta.memory.mode='off';
      const config=structuredClone(savedAgents.beta);delete config.id;config.memory.mode='read_write';
      return app.start({agentId:'beta',trial:true,config});
    }''')
    assert page.evaluate('app.state().settings')[key]['agentInvocation']['config']['memory']['mode'] == 'off'
    before = page.evaluate('app.state().tabs')
    error = page.evaluate('app.start({agentId:"missing"}).then(()=>null,error=>error.message)')
    assert error == 'Agent missing'
    assert page.evaluate('app.state().tabs') == before


@pytest.mark.parametrize('order', [(0, 1), (1, 0)])
def test_concurrent_model_picks_keep_latest_intent_without_global_write(conversation_browser, order):
    page = conversation_browser
    page.on('dialog', lambda dialog: dialog.accept())
    key = page.evaluate('app.start({agentId:"alpha"})')
    page.evaluate('key=>mount(key)', key)
    page.get_by_role('menuitem', name='Fast', exact=True).wait_for()
    page.evaluate('delayModels=true')
    page.get_by_role('menuitem', name='Fast', exact=True).click()
    page.get_by_role('menuitem', name='Reasoning', exact=True).click()
    page.wait_for_function('delayedModels.length===2')
    for index in order:
        page.evaluate('(index)=>delayedModels[index]()', index)
    page.wait_for_function('modelPicksFinished===2')
    assert page.evaluate('modelWrites') == []
    assert page.evaluate('key=>app.state().settings[key].agentInvocation.model.id', key) == 'reasoning'


def test_closed_agent_draft_does_not_fall_back_to_global_model_write(conversation_browser):
    page = conversation_browser
    key = page.evaluate('app.start({agentId:"alpha"})')
    page.evaluate('key=>mount(key)', key)
    page.get_by_role('menuitem', name='Fast', exact=True).wait_for()
    page.evaluate('delayModels=true')
    page.get_by_role('menuitem', name='Fast', exact=True).click()
    page.wait_for_function('delayedModels.length===1')
    page.evaluate('key=>app.drop(key)', key)
    page.evaluate('delayedModels[0]()')
    page.wait_for_function('modelPicksFinished===1')
    assert page.evaluate('modelWrites') == []
    assert not page.evaluate('key=>!!app.state().settings[key]', key)
