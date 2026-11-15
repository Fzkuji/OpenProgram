"""Agents editor acceptance through the actual React page, without live services."""
from pathlib import Path
import json
import re
import subprocess

import pytest

from tests.support.radix_select import choose

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser

_ENTRY = r'''
import React from 'react';
import {createRoot} from 'react-dom/client';
import {AgentsPage} from './components/agents/agents-page';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
const initial=window.agentInitial||{};
window.failList=Boolean(initial.failList);
window.agentCalls=[];
window.httpCalls=[];
window.unexpectedRequests=[];
window.failSave=false;
window.conflict=false;
window.failCreate=false;
window.failCatalog=false;
window.failConversation=false;
const makeAgent=(id,name,extra={})=>({id,name,description:'Saved description',default:id==='general',
  model:{provider:'test',id:'reasoning'},thinking_effort:'high',system_prompt:'Saved system prompt',
  skills:{allowed:['research'],disabled:[],categories:[]},tools:{mode:'automatic'},
  mcp:{allowed:['docs'],disabled:[],required:[]},identity:{name:'',mention_patterns:[]},
  memory:{mode:'read_write',read_spaces:['self'],write_space:'self',required:false},
  session_scope:'per-account-channel-peer',
  session_idle_minutes:0,session_daily_reset:'',revision:1,created_at:1,updated_at:1,...extra});
window.builtinAgents=__SAVED_BUILTIN_AGENTS__;
window.agents=initial.emptyAgents?[]:[makeAgent('general','General'),makeAgent('researcher','Researcher')];
if(initial.builtins)window.agents.push(...window.builtinAgents);
if(initial.legacyEffort&&window.agents[0])window.agents[0].model={provider:'test',id:'fast'};
if(initial.tools&&window.agents[0])window.agents[0].tools=initial.tools;
const reply=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
window.fetch=async(input,options={})=>{
  const url=String(input);const method=options.method||'GET';
  const body=options.body?JSON.parse(options.body):null;
  window.httpCalls.push({url,method,body});
  if(url==='/api/agents'&&method==='GET')return window.failList?reply({error:'Agent list temporarily unavailable'},503):reply({agents:window.agents});
  if(url==='/api/agents'&&method==='POST'){
    if(window.failCreate)return reply({error:'Create temporarily unavailable'},503);
    const agent=makeAgent('created',body.name,{default:window.agents.length===0,memory:{mode:'off',read_spaces:['self'],write_space:'self',required:false},...(body.model?{model:body.model}:{}),...(body.thinking_effort!==undefined?{thinking_effort:body.thinking_effort}:{})});window.agents.push(agent);return reply({agent});
  }
  if(url==='/api/providers/list')return reply({providers:[{id:'test',label:'Fixture provider',enabled:true,configured:true}]});
  if(url==='/api/providers/test/models')return reply({models:[
    {id:'reasoning',name:'Reasoning model',enabled:true,thinking_levels:['low','high'],default_thinking_level:'low'},
    {id:'fast',name:'Fast model',enabled:true,thinking_levels:[],default_thinking_level:''},
  ]});
  if(url==='/api/agent_settings')return reply({chat:{provider:'test',model:'reasoning'}});
  if(url==='/api/programs')return reply([{name:'research_program',description:'Research workflow',category:'workflow'}]);
  if(url==='/api/tools')return reply([{name:'read_file',description:'Read file',source:'builtin',category:'files'},{name:'web_search',description:'Search web',source:'builtin',category:'web'}]);
  if(url==='/api/tool-profiles')return reply({profiles:{FULL:['read_file','web_search']}});
  if(url==='/api/skills')return window.failCatalog?reply({error:'Skill catalog unavailable'},503):reply([{name:'research',description:'Research skill',enabled:true}]);
  if(url==='/api/agents-md')return reply({global:{path:'/fixture/home/AGENTS.md',exists:false},working_dir:'/fixture/project',loaded:[]});
  if(url==='/api/mcp/servers')return reply({servers:[{name:'docs',status:'Connected',connected:true,enabled:true}]});
  const match=url.match(/^\/api\/agents\/([^/]+)(?:\/(.*))?$/);
  if(match){
    const id=decodeURIComponent(match[1]),action=match[2];
    const agent=window.agents.find(row=>row.id===id);
    if(action==='workspace')return reply({path:'/fixture/'+id,files:[]});
    if(action==='default'&&method==='POST'){
      window.agents=window.agents.map(row=>({...row,default:row.id===id}));return reply({agent:window.agents.find(row=>row.id===id)});
    }
    if(action==='duplicate'&&method==='POST'){
      const copy=makeAgent(id+'-copy',body.name,{...agent,id:id+'-copy',name:body.name,default:false});window.agents.push(copy);return reply({agent:copy});
    }
    if(method==='DELETE'){window.agents=window.agents.filter(row=>row.id!==id);return reply({ok:true});}
    if(method==='GET')return reply({agent});
    if(method==='PATCH'){
      if(window.failSave)return reply({error:'Save temporarily unavailable'},503);
      if(window.conflict){window.conflict=false;Object.assign(agent,{revision:agent.revision+1,system_prompt:'Server system prompt'});return reply({error:'Agent changed on server',agent},409);}
      if(body.expected_revision!==agent.revision)return reply({error:'Expected revision mismatch',agent},409);
      const {expected_revision,...config}=body;
      const saved={...agent,...config,revision:agent.revision+1,updated_at:agent.updated_at+1};
      window.agents=window.agents.map(row=>row.id===id?saved:row);return reply({agent:saved});
    }
  }
  window.unexpectedRequests.push({url,method});return reply({error:'Unexpected fixture request: '+url},500);
};
let root;
window.mountAgents=()=>{
  root=createRoot(document.getElementById('mount'));
  root.render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><AgentsPage/></QueryClientProvider>);
};
window.unmountAgents=()=>{root.unmount();root=null;};
window.mountAgents();
'''

_BUILD = r'''
const fs=require('node:fs'),path=require('node:path'),esbuild=require('esbuild');
(async()=>{
  await esbuild.build({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},
    bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'css'},
    logOverride:{'css-syntax-error':'error'},
    // Next inlines its process.env flags at build time (next/link reads them);
    // without the same substitution the shared rail throws in a plain browser.
    define:{'process.env.NODE_ENV':'"development"','process.env':'{}'},
    outfile:process.argv[2],tsconfig:path.join(process.argv[1],'tsconfig.json'),
    plugins:[{name:'isolate-conversation-start',setup(build){
      build.onResolve({filter:/[\\/]agents[\\/]start-conversation(?:\.ts)?$/},()=>({path:'start-conversation',namespace:'fixture'}));
      build.onLoad({filter:/.*/,namespace:'fixture'},()=>({loader:'js',contents:`
        export async function startAgentConversation(options){
          window.agentCalls.push(structuredClone(options));
          if(window.failConversation)throw new Error('Conversation temporarily unavailable');
          return 'local_fixture_'+window.agentCalls.length;
        }`}));
    }}]});
  // Generate shared utility classes from the actual app configuration. The
  // component CSS remains esbuild's real CSS-module output, not fixture styles.
  const globals=path.join(process.argv[1],'app/globals.css');
  const css=await require('postcss')([require('@tailwindcss/postcss')({base:process.argv[1]})]).process(fs.readFileSync(globals,'utf8'),{from:globals});
  fs.writeFileSync(process.argv[2]+'.global.css',css.css);
})().catch(error=>{console.error(error);process.exit(1)});
'''


@pytest.fixture(scope='module')
def agents_bundle(tmp_path_factory):
    from openprogram.runtime.agent_registry import manager
    from openprogram.runtime.agent_registry.builtin_agents import create_builtin_agents
    directory = tmp_path_factory.mktemp('agents-configuration')
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(manager, '_state_root', lambda: directory / 'state')
        (directory / 'state').mkdir()
        manager.create('main', name='Existing default', make_default=True)
        saved_agents = [row.to_dict() for row in create_builtin_agents()]
    entry = _ENTRY.replace('__SAVED_BUILTIN_AGENTS__', json.dumps(saved_agents))
    bundle = directory / 'agents.js'
    subprocess.run(
        ['node', '-e', _BUILD, str(ROOT / 'apps/web'), str(bundle), entry],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    shell = directory / 'agents.html'
    shell.write_text('<!doctype html><html lang="en" data-theme="dark"><meta charset="utf-8"><div id="mount" class="app"></div></html>')
    return bundle, shell


@pytest.fixture
def agents_browser(agents_bundle):
    from playwright.sync_api import sync_playwright

    bundle, shell = agents_bundle
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        opened = []

        def open_page(width=1440, initial=None):
            page = browser.new_page(viewport={'width': width, 'height': 1000})
            page.set_default_timeout(5000)
            errors, network = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            # A fixture mistake must not fall through to the user's server.
            page.route('http://**/*', lambda route: (network.append(route.request.url), route.abort()))
            page.route('https://**/*', lambda route: (network.append(route.request.url), route.abort()))
            page.goto(shell.as_uri())
            page.evaluate("localStorage.setItem('agentic_locale','en')")
            page.evaluate('initial=>{window.agentInitial=initial}', initial or {})
            page.add_style_tag(path=str(bundle) + '.global.css')
            page.add_style_tag(path=str(bundle.with_suffix('.css')))
            page.add_script_tag(path=str(bundle))
            opened.append((page, errors, network))
            return page

        yield open_page
        try:
            for page, errors, network in opened:
                assert not errors, errors
                assert not network, network
                assert not page.evaluate('window.unexpectedRequests'), page.evaluate('window.unexpectedRequests')
        finally:
            browser.close()


def _tab(page, name):
    page.get_by_role('tab', name=name, exact=True).click()


def _agent(page, name):
    return page.get_by_role('complementary', name='Agent list').get_by_role('button', name=re.compile(name))


def _card(page, name):
    # A card's accessible name starts with the Agent's display name and id.
    return page.get_by_role('button', name=re.compile('^' + re.escape(name) + ' '))


def _quick_panel(page):
    # The panel is named after the draft, so the name follows edits to it.
    return page.get_by_role('complementary', name=re.compile('^Quick settings for '))


def open_quick_settings(page, agent_name='General'):
    """The page opens on the card grid; a card opens its quick settings."""
    from playwright.sync_api import expect

    _card(page, agent_name).click()
    panel = _quick_panel(page)
    expect(panel).to_be_visible()
    return panel


def open_editor(page, agent_name='General', tab='General'):
    """Quick settings first, then their Manage control opens the full editor.

    Manage is used rather than the width handle because the handle is hidden
    on narrow screens, while Manage is there at every width.
    """
    open_quick_settings(page, agent_name).get_by_role('button', name='Manage', exact=True).click()
    _wait_for_editor_frame(page)
    if tab != 'Programs':
        _tab(page, tab)


def _wait_for_editor_frame(page):
    # The quick panel grows into the editor through layout transforms, so
    # geometry checks start only once no transform is left and the editor
    # keeps its place between two animation frames.
    from playwright.sync_api import expect

    expect(page.get_by_role('tablist', name='Agent configuration')).to_be_visible()
    page.wait_for_function('''() => new Promise(resolve => {
      const editor = document.querySelector('main');
      const moving = () => [editor, ...editor.querySelectorAll('*')].some(element => element.style.transform && element.style.transform !== 'none');
      const before = JSON.stringify(editor.getBoundingClientRect());
      requestAnimationFrame(() => requestAnimationFrame(() => resolve(!moving() && JSON.stringify(editor.getBoundingClientRect()) === before)));
    })''')


def _new_agent_button(page):
    # The header action comes first; the grid's trailing card is a second entry point.
    return page.get_by_role('button', name='New Agent', exact=True).first


def _patches(page):
    return page.evaluate("window.httpCalls.filter(call=>call.method==='PATCH')")


def test_agents_exposes_memory_configuration(agents_browser):
    from playwright.sync_api import expect

    page = agents_browser()
    open_editor(page)
    expect(page.get_by_role('tab', name='Memory', exact=True)).to_be_visible()


@pytest.mark.parametrize('theme', ['beige-dark', 'beige-light', 'dark', 'light', 'aurora', 'aurora-light', 'vivid', 'vivid-light'])
def test_agent_fields_have_one_flush_focus_border(agents_browser, theme):
    from playwright.sync_api import expect

    page = agents_browser()
    open_editor(page)
    page.locator('html').evaluate('(el, theme) => el.dataset.theme = theme', theme)
    fields = [page.get_by_role('textbox', name='Display name', exact=True),
              page.get_by_role('textbox', name='Description', exact=True)]
    for field in fields:
        expect(field).to_be_visible()
        before = field.bounding_box()
        field.click()
        expect(field).to_be_focused()
        style = field.evaluate('el => { const s = getComputedStyle(el); return {outline: s.outlineStyle, shadow: s.boxShadow, border: s.borderTopWidth}; }')
        assert style == {'outline': 'none', 'shadow': 'none', 'border': '1px'}
        assert field.bounding_box() == before
        page.keyboard.press('Tab')
        page.keyboard.press('Shift+Tab')
        expect(field).to_be_focused()
        assert field.evaluate('el => getComputedStyle(el).outlineStyle') == 'none'
    search = page.get_by_role('textbox', name='Search Agents', exact=True)
    search.click()
    assert search.evaluate('el => getComputedStyle(el).borderTopWidth') == '0px'
    assert search.evaluate('el => getComputedStyle(el).outlineStyle') == 'none'
    assert search.evaluate('el => getComputedStyle(el.parentElement).borderTopWidth') == '1px'
    _tab(page, 'Context')
    for field in [page.get_by_label('Session scope'),
                  page.get_by_label('Idle reset (minutes)'), page.get_by_label('Daily reset')]:
        before = field.bounding_box()
        field.focus()
        expect(field).to_be_focused()
        assert field.evaluate('el => { const s = getComputedStyle(el); return [s.outlineStyle, s.boxShadow, s.borderTopWidth]; }') == ['none', 'none', '1px']
        assert field.bounding_box() == before



@pytest.mark.parametrize('width', [1440, 1000])
def test_general_edits_parameters_without_prompt_editor(agents_browser, width):
    from playwright.sync_api import expect

    page = agents_browser(width)
    open_editor(page)
    shared_width = page.evaluate("parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width'))")
    assert abs(page.get_by_role('complementary', name='Agent list').bounding_box()['width'] - (shared_width - 1)) <= 1
    expect(page.get_by_role('textbox', name='Display name', exact=True)).to_be_visible()
    expect(page.get_by_label('Model', exact=True)).to_be_visible()
    expect(page.get_by_role('textbox', name=re.compile('^System prompt'))).to_have_count(0)
    expect(page.get_by_role('tablist', name='Agent configuration')).to_have_attribute('aria-orientation', 'horizontal')
    assert page.get_by_role('button', name='Edit Model', exact=True).count() == 0
    assert page.get_by_role('tablist', name='Agent configuration').bounding_box()['height'] < 60
    assert page.get_by_role('textbox', name='Display name', exact=True).evaluate('el=>getComputedStyle(el).fontWeight') == '400'
    expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_visible()
    # On a wide screen the form uses the editor width, retaining only its
    # standard page padding rather than leaving a fixed-width empty column.
    panel = page.get_by_role('tabpanel', name='General', exact=True)
    assert panel.evaluate('el=>Math.abs(el.parentElement.clientWidth-el.clientWidth)') <= 1
    _tab(page, 'Advanced')
    expect(page.get_by_role('textbox', name=re.compile('^System prompt'))).to_have_value('Saved system prompt')
    assert page.get_by_role('textbox', name=re.compile('^System prompt')).evaluate('el=>getComputedStyle(el).minHeight') == '160px'


@pytest.mark.parametrize('width', [1440, 390])
def test_agents_keyboard_memory_and_responsive_layout(agents_browser, width):
    from playwright.sync_api import expect

    page = agents_browser(width)
    open_editor(page)
    tabs = page.get_by_role('tablist', name='Agent configuration')
    expect(tabs.get_by_role('tab')).to_have_count(8)
    overview = tabs.get_by_role('tab', name='General', exact=True)
    overview.focus()
    page.keyboard.press('End')
    expect(tabs.get_by_role('tab', name='Advanced', exact=True)).to_be_focused()
    page.keyboard.press('Home')
    expect(overview).to_be_focused()
    expect(overview).to_have_attribute('aria-selected', 'true')
    navigation = page.get_by_role('tablist', name='Agent configuration')
    expect(navigation).to_have_attribute('aria-orientation', 'horizontal')
    page.keyboard.press('ArrowRight')
    expect(page.get_by_role('tab', name='Programs', exact=True)).to_be_focused()
    resized_width = 1280 if width <= 800 else 390
    page.set_viewport_size({'width': resized_width, 'height': 1000})
    expect(navigation).to_have_attribute('aria-orientation', 'horizontal')
    overview.focus()
    page.keyboard.press('ArrowRight')
    expect(page.get_by_role('tab', name='Programs', exact=True)).to_be_focused()
    page.set_viewport_size({'width': width, 'height': 1000})
    _tab(page, 'Memory')
    off = page.get_by_role('radio', name=re.compile('^Off'))
    readonly = page.get_by_role('radio', name=re.compile('^Read only'))
    off.check()
    expect(page.get_by_role('checkbox', name=re.compile('^This Agent'))).to_be_disabled()
    off.focus()
    # Radix moves focus a task after keydown and checks the radio only while
    # the arrow is still held, as it is for a person; an instant press
    # releases the key before that.
    page.keyboard.down('ArrowRight')
    expect(readonly).to_be_checked()
    expect(readonly).to_be_focused()
    page.keyboard.up('ArrowRight')
    expect(page.get_by_role('combobox', name='Write to', exact=True)).to_be_disabled()
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_disabled()
    assert _patches(page)[-1]['body']['memory']['mode'] == 'read_only'
    _tab(page, 'General')
    page.get_by_label('Model', exact=True).click()
    dialog = page.get_by_role('dialog', name='Choose model', exact=True)
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role('textbox', name='Search models')).to_be_focused()
    # Focus stays inside the Radix dialog, including on a narrow screen.
    for _ in range(8):
        page.keyboard.press('Tab')
        assert dialog.evaluate('el=>el.contains(document.activeElement)')
    page.keyboard.press('Escape')
    expect(dialog).not_to_be_visible()
    expect(page.get_by_label('Model', exact=True)).to_be_focused()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')


def test_agent_draft_spans_tabs_and_survives_save_failure(agents_browser):
    from playwright.sync_api import expect

    page = agents_browser()
    open_editor(page)
    page.get_by_role('textbox', name='Display name', exact=True).fill('General edited')
    page.get_by_role('textbox', name='Description', exact=True).fill('Local description')
    _tab(page, 'Memory')
    page.get_by_role('radio', name=re.compile('^Off')).check()
    _tab(page, 'Advanced')
    prompt = page.get_by_role('textbox', name=re.compile('^System prompt'))
    prompt.fill('Draft instructions')
    _tab(page, 'General')
    expect(page.get_by_role('textbox', name='Display name', exact=True)).to_have_value('General edited')
    page.evaluate('window.failSave=true')
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('Save temporarily unavailable')
    expect(page.get_by_role('textbox', name='Description', exact=True)).to_have_value('Local description')
    failed = _patches(page)[-1]['body']
    assert failed['expected_revision'] == 1
    assert failed['name'] == 'General edited'
    assert failed['system_prompt'] == 'Draft instructions'
    assert failed['memory']['mode'] == 'off'
    page.evaluate('window.failSave=false')
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_disabled()
    _tab(page, 'Memory')
    expect(page.get_by_role('radio', name=re.compile('^Off'))).to_be_checked()
    _tab(page, 'General')
    page.get_by_role('textbox', name='Description', exact=True).fill('Discard this value')
    page.get_by_role('button', name='Discard changes', exact=True).click()
    expect(page.get_by_role('textbox', name='Description', exact=True)).to_have_value('Local description')
    assert len(_patches(page)) == 2


def test_agent_conflict_preserves_draft_until_explicit_choice(agents_browser):
    from playwright.sync_api import expect

    page = agents_browser()
    open_editor(page, tab='Advanced')
    prompt = page.get_by_role('textbox', name=re.compile('^System prompt'))
    prompt.fill('Local conflicting instructions')
    page.evaluate('window.conflict=true')
    page.get_by_role('button', name='Save changes', exact=True).click()
    dialog = page.get_by_role('dialog')
    expect(dialog).to_contain_text('Local conflicting instructions')
    expect(dialog).to_contain_text('Server system prompt')
    assert len(_patches(page)) == 1
    dialog.get_by_role('button', name=re.compile('Keep.*draft', re.I)).click()
    expect(prompt).to_have_value('Local conflicting instructions')
    # Keeping the local draft is not an overwrite request. Save is deliberate.
    assert len(_patches(page)) == 1
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_disabled()
    assert _patches(page)[-1]['body']['expected_revision'] == 2
    prompt.fill('Local second conflict')
    page.evaluate('window.conflict=true')
    page.get_by_role('button', name='Save changes', exact=True).click()
    page.get_by_role('dialog').get_by_role('button', name=re.compile('Load.*server', re.I)).click()
    expect(prompt).to_have_value('Server system prompt')
    expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_disabled()
    assert len(_patches(page)) == 3


def test_agent_model_change_keeps_incompatible_effort(agents_browser):
    from playwright.sync_api import expect

    page = agents_browser()
    open_editor(page)
    effort = page.get_by_role('combobox', name='Thinking effort', exact=True)
    expect(effort).to_have_text('high')
    page.get_by_label('Model', exact=True).click()
    dialog = page.get_by_role('dialog', name='Choose model')
    dialog.get_by_role('textbox', name='Search models').fill('Fast')
    dialog.get_by_role('button', name=re.compile('^Fast model')).click()
    expect(effort).to_have_text('high — not supported by this model')
    expect(page.get_by_role('alert').filter(has_text='Your previous choice has been kept')).to_be_visible()
    expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_disabled()
    assert not _patches(page)
    choose(effort, re.compile('^Inherit model default'))
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_disabled()
    body = _patches(page)[-1]['body']
    assert body['model'] == {'provider': 'test', 'id': 'fast'}
    assert body['thinking_effort'] == ''
    assert not page.evaluate("window.httpCalls.some(call=>call.url==='/api/model'||(call.url==='/api/agent_settings'&&call.method!=='GET'))")


def test_agent_trial_uses_unsaved_readonly_snapshot_and_chat_uses_saved_id(agents_browser):
    from playwright.sync_api import expect

    page = agents_browser()
    open_editor(page)
    page.get_by_role('button', name='New conversation', exact=True).click()
    page.wait_for_function('window.agentCalls.length===1')
    assert page.evaluate('window.agentCalls[0]') == {'agentId': 'general'}
    _tab(page, 'Advanced')
    prompt = page.get_by_role('textbox', name=re.compile('^System prompt'))
    prompt.fill('Unsaved trial instructions')
    page.get_by_role('button', name='Try in new conversation', exact=True).click()
    page.wait_for_function('window.agentCalls.length===2')
    trial = page.evaluate('window.agentCalls[1]')
    assert trial['trial'] is True
    assert trial['config']['system_prompt'] == 'Unsaved trial instructions'
    assert trial['config']['memory']['mode'] == 'read_only'
    assert 'revision' not in trial['config']
    assert not _patches(page)
    expect(prompt).to_have_value('Unsaved trial instructions')
    _tab(page, 'Memory')
    expect(page.get_by_role('radio', name=re.compile('^Read and write'))).to_be_checked()
    page.get_by_role('radio', name=re.compile('^Off')).check()
    page.get_by_role('button', name='Try in new conversation', exact=True).click()
    page.wait_for_function('window.agentCalls.length===3')
    assert page.evaluate('window.agentCalls[2].config.memory.mode') == 'off'
    page.evaluate('window.failConversation=true')
    page.get_by_role('button', name='Try in new conversation', exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('Conversation temporarily unavailable')
    expect(page.get_by_role('radio', name=re.compile('^Off'))).to_be_checked()
    expect(page.get_by_role('button', name='Try in new conversation', exact=True)).to_be_enabled()
    assert not _patches(page)


def test_agent_empty_capability_selection_is_persisted_as_none(agents_browser):
    from playwright.sync_api import expect

    page = agents_browser()
    open_editor(page, tab='Skills')
    assert not page.evaluate("window.httpCalls.some(call=>call.url==='/api/skills')")
    page.get_by_role('button', name=re.compile('Browse Skills')).click()
    dialog = page.get_by_role('dialog', name='Manage Skills')
    research = dialog.get_by_role('checkbox', name=re.compile('^research'))
    expect(research).to_be_checked()
    research.uncheck()
    dialog.get_by_role('button', name='Done', exact=True).click()
    expect(page.get_by_role('radio', name=re.compile('^No Skills'))).to_be_checked()
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_disabled()
    policy = _patches(page)[-1]['body']['skills']
    assert policy['allowed'] == []
    assert policy['disabled'] == ['*']
    _tab(page, 'MCP')
    page.get_by_role('button', name=re.compile('Browse MCP')).click()
    dialog = page.get_by_role('dialog', name='Manage MCP')
    dialog.get_by_role('button', name='Optional', exact=True).click()
    dialog.get_by_role('checkbox', name=re.compile('^docs')).uncheck()
    dialog.get_by_role('button', name='Done', exact=True).click()
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_disabled()
    policy = _patches(page)[-1]['body']['mcp']
    assert policy['allowed'] == []
    assert policy['disabled'] == ['*']
    assert policy['required'] == []
    # A failed catalog request must not erase the saved policy or enable all.
    _tab(page, 'Skills')
    page.evaluate('window.failCatalog=true')
    page.get_by_role('button', name=re.compile('Browse Skills')).click()
    dialog = page.get_by_role('dialog', name='Manage Skills')
    expect(dialog).to_contain_text('Skill catalog unavailable')
    dialog.get_by_role('button', name='Done', exact=True).click()
    expect(page.get_by_role('radio', name=re.compile('^No Skills'))).to_be_checked()
    assert len(_patches(page)) == 2


def test_agent_switch_and_create_require_explicit_draft_choice(agents_browser):
    from playwright.sync_api import expect

    page = agents_browser(390)
    # On a narrow screen the cards are how one switches Agents.
    name = open_quick_settings(page).get_by_role('textbox', name='Display name', exact=True)
    name.fill('Uncommitted general')
    _card(page, 'Researcher').click()
    dialog = page.get_by_role('dialog')
    expect(dialog).to_be_visible()
    dialog.get_by_role('button', name='Cancel', exact=True).click()
    expect(name).to_have_value('Uncommitted general')
    assert not _patches(page)
    _card(page, 'Researcher').click()
    page.get_by_role('dialog').get_by_role('button', name=re.compile('^Save')).click()
    expect(name).to_have_value('Researcher')
    assert _patches(page)[-1]['body']['name'] == 'Uncommitted general'
    name.fill('Unsaved research')
    _new_agent_button(page).click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('button', name='Cancel', exact=True).click()
    expect(name).to_have_value('Unsaved research')
    _new_agent_button(page).click()
    page.get_by_role('dialog').get_by_role('button', name=re.compile('^Discard')).click()
    create_dialog = page.get_by_role('dialog', name='New Agent', exact=True)
    create_name = create_dialog.get_by_role('textbox')
    create_name.fill('New analyst')
    page.evaluate('window.failCreate=true')
    create_dialog.get_by_role('button', name='Create', exact=True).click()
    expect(create_dialog).to_contain_text('Create temporarily unavailable')
    expect(create_name).to_have_value('New analyst')
    page.evaluate('window.failCreate=false')
    create_dialog.get_by_role('button', name='Create', exact=True).click()
    expect(create_dialog).not_to_be_visible()
    expect(name).to_have_value('New analyst')
    creates = page.evaluate("window.httpCalls.filter(call=>call.method==='POST'&&call.url==='/api/agents')")
    assert creates[-1]['body'] == {'name': 'New analyst', 'model': {'provider': '', 'id': ''}, 'thinking_effort': ''}


def test_agent_lifecycle_actions_guard_drafts_and_keep_default_protected(agents_browser):
    from playwright.sync_api import expect

    page = agents_browser()
    open_editor(page)
    actions = page.get_by_label('Agent actions', exact=True)
    actions.click()
    expect(page.get_by_role('menuitem', name='Delete Agent', exact=True)).to_be_disabled()
    # The menu is modal, so it closes from the keyboard rather than its button.
    page.keyboard.press('Escape')
    expect(page.get_by_role('menu')).to_have_count(0)
    _agent(page, 'Researcher').click()
    name = page.get_by_role('textbox', name='Display name', exact=True)
    name.fill('Uncommitted researcher')
    for action in ['Set as default', 'Duplicate Agent', 'Delete Agent']:
        actions.click()
        page.get_by_role('menuitem', name=action, exact=True).click()
        dialog = page.get_by_role('dialog', name='Unsaved changes', exact=True)
        expect(dialog).to_be_visible()
        dialog.get_by_role('button', name='Cancel', exact=True).click()
        expect(name).to_have_value('Uncommitted researcher')
        assert not page.evaluate("window.httpCalls.some(call=>call.method!=='GET')")
        expect(page.get_by_role('menuitem', name='Duplicate Agent', exact=True)).not_to_be_visible()
    actions.click()
    page.get_by_role('menuitem', name='Duplicate Agent', exact=True).click()
    page.get_by_role('dialog', name='Unsaved changes').get_by_role('button', name='Discard', exact=True).click()
    duplicate = page.get_by_role('dialog', name='Duplicate Agent', exact=True)
    duplicate.get_by_role('textbox', name='Agent name', exact=True).fill('Independent copy')
    duplicate.get_by_role('button', name='Duplicate', exact=True).click()
    expect(duplicate).not_to_be_visible()
    expect(name).to_have_value('Independent copy')
    duplicate_call = page.evaluate("window.httpCalls.find(call=>call.url==='/api/agents/researcher/duplicate')")
    assert duplicate_call['body'] == {'name': 'Independent copy'}
    assert page.evaluate("window.agents.find(agent=>agent.id==='researcher').name") == 'Researcher'
    assert not _patches(page)
    actions.click()
    page.get_by_role('menuitem', name='Delete Agent', exact=True).click()
    deletion = page.get_by_role('dialog', name='Delete Agent', exact=True)
    confirm = deletion.get_by_role('textbox')
    confirm.fill('wrong-id')
    expect(deletion.get_by_role('button', name='Delete Agent', exact=True)).to_be_disabled()
    confirm.fill('researcher-copy')
    deletion.get_by_role('button', name='Delete Agent', exact=True).click()
    expect(deletion).not_to_be_visible()
    # Deleting returns to the grid with the default Agent selected again.
    expect(_card(page, 'Independent copy')).to_have_count(0)
    expect(open_quick_settings(page).get_by_role('textbox', name='Display name', exact=True)).to_have_value('General')
    assert page.evaluate("window.httpCalls.filter(call=>call.method==='DELETE').map(call=>call.url)") == ['/api/agents/researcher-copy']
    open_editor(page, 'Researcher')
    actions.click()
    page.get_by_role('menuitem', name='Set as default', exact=True).click()
    expect(page.get_by_role('menuitem', name='Set as default', exact=True)).not_to_be_visible()
    actions.click()
    expect(page.get_by_role('menuitem', name='Set as default', exact=True)).to_be_disabled()
    assert page.evaluate("window.agents.find(agent=>agent.default).id") == 'researcher'



@pytest.mark.parametrize('opener', ['browse', 'selected-radio'])
@pytest.mark.parametrize('close', ['done', 'escape'])
def test_agent_catalog_restores_focus_to_its_opener(agents_browser, opener, close):
    from playwright.sync_api import expect

    page = agents_browser(390 if opener == 'selected-radio' else 1440)
    open_editor(page, tab='Skills')
    if opener == 'selected-radio':
        page.get_by_role('radio', name=re.compile('^No Skills')).check()
        trigger = page.get_by_role('radio', name=re.compile('^Selected scope'))
    else:
        trigger = page.get_by_role('button', name=re.compile('^Browse Skills'))
    trigger.click()
    dialog = page.get_by_role('dialog', name='Manage Skills', exact=True)
    expect(dialog).to_be_visible()
    if close == 'done':
        dialog.get_by_role('button', name='Done', exact=True).click()
    else:
        page.keyboard.press('Escape')
    expect(dialog).not_to_be_visible()
    expect(trigger).to_be_focused()
    assert not _patches(page)


def test_agent_draft_remount_preserves_edits_and_detects_server_revision(agents_browser):
    from playwright.sync_api import expect

    page = agents_browser()
    open_editor(page, tab='Advanced')
    prompt = page.get_by_role('textbox', name=re.compile('^System prompt'))
    prompt.fill('Keep this draft while visiting another page')
    _tab(page, 'Memory')
    page.get_by_role('radio', name=re.compile('^Off')).check()
    # Recreate the actual page, retaining only module-level application state.
    # This models leaving Agents and returning without refreshing the app.
    page.evaluate('window.unmountAgents()')
    expect(page.get_by_role('tab')).to_have_count(0)
    page.evaluate('window.mountAgents()')
    open_editor(page, tab='Memory')
    expect(page.get_by_role('radio', name=re.compile('^Off'))).to_be_checked()
    _tab(page, 'Advanced')
    expect(prompt).to_have_value('Keep this draft while visiting another page')
    assert not _patches(page)
    page.evaluate('window.unmountAgents()')
    page.evaluate("Object.assign(window.agents[0],{revision:2,system_prompt:'Changed while editor was closed'})")
    page.evaluate('window.mountAgents()')
    conflict = page.get_by_role('dialog', name='Configuration changed on the server', exact=True)
    expect(conflict).to_contain_text('Keep this draft while visiting another page')
    expect(conflict).to_contain_text('Changed while editor was closed')
    assert not _patches(page)
    conflict.get_by_role('button', name='Keep my draft', exact=True).click()
    open_editor(page, tab='Advanced')
    expect(prompt).to_have_value('Keep this draft while visiting another page')
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_disabled()
    assert _patches(page)[-1]['body']['expected_revision'] == 2
    assert _patches(page)[-1]['body']['memory']['mode'] == 'off'


def test_agent_initial_load_failure_retries_without_creating_data(agents_browser):
    from playwright.sync_api import expect

    page = agents_browser(initial={'failList': True})
    expect(page.get_by_role('alert')).to_contain_text('Agent list temporarily unavailable')
    expect(_new_agent_button(page)).to_be_disabled()
    expect(page.get_by_role('tab')).to_have_count(0)
    assert not page.evaluate("window.httpCalls.some(call=>call.method!=='GET')")
    page.evaluate('window.failList=false')
    page.get_by_role('button', name='Retry', exact=True).click()
    expect(open_quick_settings(page).get_by_role('textbox', name='Display name', exact=True)).to_have_value('General')
    expect(page.get_by_role('alert')).to_have_count(0)
    assert page.evaluate("window.httpCalls.filter(call=>call.url==='/api/agents'&&call.method==='GET').length") == 2
    assert not page.evaluate("window.httpCalls.some(call=>call.method!=='GET')")


def test_agent_empty_list_can_create_first_configuration(agents_browser):
    from playwright.sync_api import expect

    page = agents_browser(initial={'emptyAgents': True})
    expect(page.get_by_role('heading', name='Create your first Agent', exact=True)).to_be_visible()
    expect(page.get_by_role('tab')).to_have_count(0)
    # The empty state's own button, after the header action.
    page.get_by_role('button', name='New Agent', exact=True).last.click()
    dialog = page.get_by_role('dialog', name='New Agent', exact=True)
    dialog.get_by_role('textbox', name='Agent name', exact=True).fill('First Agent')
    dialog.get_by_role('button', name='Create', exact=True).click()
    expect(dialog).not_to_be_visible()
    # The new Agent opens in quick settings; the editor is one step further.
    expect(_quick_panel(page).get_by_role('textbox', name='Display name', exact=True)).to_have_value('First Agent')
    _quick_panel(page).get_by_role('button', name='Manage', exact=True).click()
    _wait_for_editor_frame(page)
    expect(page.get_by_role('tab')).to_have_count(8)
    _tab(page, 'Memory')
    expect(page.get_by_role('radio', name=re.compile('^Off'))).to_be_checked()
    assert page.evaluate("window.httpCalls.filter(call=>call.method==='POST').map(call=>call.body)") == [{'name': 'First Agent', 'model': {'provider': '', 'id': ''}, 'thinking_effort': ''}]


@pytest.mark.parametrize('change', ['toggle', 'all', 'none', 'preset'])
def test_program_changes_preserve_explicit_web_search_setting(agents_browser, monkeypatch, change):
    from types import SimpleNamespace
    from playwright.sync_api import expect
    from openprogram.runtime.conversation.model_tools import resolve_tools
    import openprogram.programs

    initial = {
        'mode': 'selected', 'allowed': ['read_file', 'web_search'],
        'disabled': ['secret*'], 'web_search': False,
    }
    page = agents_browser(initial={'tools': initial})
    open_editor(page, tab='Programs')
    if change in ('toggle', 'preset'):
        page.get_by_role('button', name='Browse Programs…', exact=True).click()
        dialog = page.get_by_role('dialog', name='Manage Programs', exact=True)
        if change == 'toggle':
            dialog.get_by_role('checkbox', name='read_file Read file', exact=True).uncheck()
        else:
            choose(dialog.get_by_role('combobox', name='Access preset', exact=True), 'FULL')
        dialog.get_by_role('button', name='Done', exact=True).click()
    else:
        page.get_by_role('radio', name=re.compile('^All Programs' if change == 'all' else '^No Programs')).check()
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_disabled()
    saved = _patches(page)[-1]['body']['tools']
    assert saved.get('web_search') is False

    # The real production resolver must still exclude Search after a change
    # to another Program, mode or Access preset. Only the tool registry is fake.
    def registered_tools(names=None, **_kwargs):
        selected = names if names is not None else ['read_file', 'web_search']
        return [SimpleNamespace(name=name) for name in selected]

    monkeypatch.setattr(openprogram.programs, 'agent_tools', registered_tools)
    assert 'web_search' not in [tool.name for tool in resolve_tools({'tools': initial}) or []]
    assert 'web_search' not in [tool.name for tool in resolve_tools({'tools': saved}) or []]


def test_old_save_response_preserves_newer_remounted_draft(agents_browser):
    from playwright.sync_api import expect

    page = agents_browser()
    open_editor(page)
    description = page.get_by_role('textbox', name='Description', exact=True)
    description.fill('Submitted edit')
    page.evaluate("""() => {
      const fetch = window.fetch;
      let delayed = false;
      window.fetch = async (input, options = {}) => {
        if (options.method === 'PATCH' && !delayed) {
          delayed = true;
          await new Promise(resolve => { window.releaseSave = resolve; });
        }
        return fetch(input, options);
      };
    }""")
    page.get_by_role('button', name='Save changes', exact=True).click()
    page.wait_for_function('typeof window.releaseSave === "function"')
    page.evaluate('window.unmountAgents()')
    page.evaluate('window.mountAgents()')
    open_editor(page)
    expect(description).to_have_value('Submitted edit')
    description.fill('New edit after returning')
    page.evaluate('window.releaseSave()')
    page.wait_for_function('window.agents[0].revision === 2')
    expect(description).to_have_value('New edit after returning')
    page.evaluate('window.unmountAgents()')
    page.evaluate('window.mountAgents()')
    conflict = page.get_by_role('dialog', name='Configuration changed on the server', exact=True)
    expect(conflict).to_contain_text('New edit after returning')
    expect(conflict).to_contain_text('Submitted edit')
    assert len(_patches(page)) == 1
    conflict.get_by_role('button', name='Keep my draft', exact=True).click()
    open_editor(page)
    expect(description).to_have_value('New edit after returning')
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_disabled()
    assert _patches(page)[-1]['body']['expected_revision'] == 2
    assert page.evaluate('window.agents[0].description') == 'New edit after returning'


def test_saved_specialists_and_blank_creation(agents_browser, tmp_path):
    from playwright.sync_api import expect

    page = agents_browser(1280, initial={"builtins": True})
    # The built-in Agents are ordinary cards: the three specialists and the job Agents.
    for name in ("Decision", "Lightweight helper", "Coordinator", "Chief of Staff", "Research Desk", "Inbox Triage", "Writer"):
        expect(_card(page, name)).to_be_visible()
    expect(_card(page, 'Image creator')).to_have_count(0)
    open_editor(page, 'Lightweight helper')
    expect(page.get_by_role('heading', name='Lightweight helper', exact=True)).to_be_visible()
    page.screenshot(animations='disabled', path=str(tmp_path / 'agents-desktop-dark.png'))
    page.locator('html').evaluate("el=>el.dataset.theme='light'")
    page.screenshot(animations='disabled', path=str(tmp_path / 'agents-desktop.png'))
    _tab(page, 'Advanced')
    expect(page.get_by_role('textbox', name=re.compile('^System prompt'))).to_have_value('')
    page.get_by_role('button', name='New Agent', exact=True).click()
    dialog = page.get_by_role('dialog', name='New Agent', exact=True)
    # A new Agent is a name and a model; there is no template picker.
    expect(dialog.get_by_role('radiogroup')).to_have_count(0)
    dialog.get_by_role('textbox', name='Agent name', exact=True).fill('Small tasks')
    dialog.get_by_label('Model', exact=True).click()
    model_dialog = page.get_by_role('dialog', name='Choose model', exact=True)
    model_dialog.get_by_role('button', name=re.compile('^Fast model')).click()
    assert not page.evaluate("window.httpCalls.some(call=>call.method==='POST')")
    dialog.get_by_role('button', name='Create', exact=True).click()
    expect(dialog).not_to_be_visible()
    created = page.evaluate("window.agents.find(agent=>agent.id==='created')")
    assert created['model'] == {'provider': 'test', 'id': 'fast'}
    assert created['memory']['mode'] == 'off'
    posts = page.evaluate("window.httpCalls.filter(call=>call.method==='POST')")
    assert len(posts) == 1 and 'role' not in posts[0]['body'] and 'template_id' not in posts[0]['body']


def test_saved_legacy_effort_does_not_show_pre_edit_error(agents_browser, tmp_path):
    from playwright.sync_api import expect

    page = agents_browser(390, initial={"legacyEffort": True, "builtins": True})
    open_editor(page)
    expect(page.get_by_role('alert')).to_have_count(0)
    expect(page.get_by_role('heading', name='General', exact=True)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(animations='disabled', path=str(tmp_path / 'agents-narrow.png'))
    page.get_by_role('textbox', name='Description', exact=True).fill('Updated purpose')
    expect(page.get_by_role('alert').filter(has_text='Choose a supported thinking effort')).to_be_visible()
