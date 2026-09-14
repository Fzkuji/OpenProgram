"""Bounded chat loading preserves transcript metadata without drawing a hidden DAG."""
import asyncio
import json
import os
from pathlib import Path
import time

from openprogram.store import SessionStore
from openprogram.webui.ws_actions import session as ws_session
from openprogram.webui.session_history import close_snapshots


def test_cold_bounded_load_profile(tmp_path, monkeypatch):
    from openprogram.webui import server
    count = int(os.environ.get('PROFILE_CHAT_COUNT', '100'))
    root = tmp_path / 'sessions'
    store = SessionStore(root)
    store.create_session('perf-chat', 'main')
    for i in range(count):
        store.append_message('perf-chat', {'id':f'm{i}', 'role':'user' if i%2==0 else 'assistant',
            'content':f'Message {i}: '+'ordinary text '*20, 'predecessor':f'm{i-1}' if i else None})
    store.update_session('perf-chat', head_id=f'm{count-1}')
    store = SessionStore(root)
    monkeypatch.setattr('openprogram.agent.session_db.default_db', lambda:store)
    monkeypatch.setattr(server, '_get_provider_info', lambda _sid=None:{})
    monkeypatch.setattr(server, '_is_run_active', lambda _sid:False)
    monkeypatch.setattr(server, 'refresh_context_stats', lambda _sid:None)
    timings={}
    from openprogram.webui import graph_builder
    graph_calls=[]
    graph_original=graph_builder.build_session_graph
    def graph_probe(*args,**kwargs):
        graph_calls.append(kwargs)
        start=time.perf_counter()
        result=graph_original(*args,**kwargs)
        timings['build_session_graph']=time.perf_counter()-start
        return result
    monkeypatch.setattr(graph_builder,'build_session_graph',graph_probe)
    original=ws_session._session_io
    async def timed(func,*args,**kwargs):
        start=time.perf_counter()
        result=await original(func,*args,**kwargs)
        key=getattr(func,'__name__',str(func))
        timings[key]=timings.get(key,0)+time.perf_counter()-start
        return result
    monkeypatch.setattr(ws_session,'_session_io',timed)
    class WS:
        _bounded_history=True
        _history_protocol=1
        def __init__(self):self.loaded=None;self.first=None
        async def send_text(self,text):
            frame=json.loads(text)
            if frame['type']=='session_loaded':self.loaded=frame['data'];self.first=time.perf_counter()
    ws=WS()
    with server._sessions_lock:server._sessions['perf-chat']={'id':'perf-chat'}
    start=time.perf_counter()
    try:
        asyncio.run(ws_session.handle_load_session(ws,{'session_id':'perf-chat'}))
        assert ws.loaded['messages'][-1]['id']==f'm{count-1}'
        assert len(ws.loaded['messages'])<=50
        assert ws.loaded['graph']==[]
        if not os.environ.get('PROFILE_CHAT_ONLY'):
            assert graph_calls==[], "ordinary bounded Chat must not build an unused graph"
        record={'messages':count,'first_payload_seconds':ws.first-start,'phases_seconds':timings}
        print(json.dumps(record))
        if path:=os.environ.get('PROFILE_CHAT_OUTPUT'):
            Path(path).write_text(json.dumps(record,indent=2))
    finally:
        close_snapshots(ws)
        with server._sessions_lock:server._sessions.pop('perf-chat',None)


def test_bounded_compaction_matches_legacy_transcript(tmp_path, monkeypatch):
    from openprogram.webui import server, graph_builder
    from openprogram.webui.session_history import wire_message
    from openprogram.context.persistence import SUMMARY_NODE_NAME
    store=SessionStore(tmp_path/'sessions')
    store.create_session('compacted-perf','main')
    for i in range(6):
        store.append_message('compacted-perf',{'id':f'm{i}','role':'user' if i%2==0 else 'assistant',
            'content':str(i),'predecessor':f'm{i-1}' if i else None})
    store.append_message('compacted-perf',{'id':'summary','role':'llm','token_model':SUMMARY_NODE_NAME,
        'content':'Complete summary','extra':{'covers_ids':['m0','m1'],'summarised_count':2}})
    store.update_session('compacted-perf',head_id='m5')
    monkeypatch.setattr('openprogram.agent.session_db.default_db',lambda:store)
    monkeypatch.setattr(server,'_get_provider_info',lambda _sid=None:{})
    monkeypatch.setattr(server,'_is_run_active',lambda _sid:False)
    monkeypatch.setattr(server,'refresh_context_stats',lambda _sid:None)
    calls=[]
    original=graph_builder.build_session_graph
    def build(*args,**kwargs):calls.append(kwargs.get('include_layout',True));return original(*args,**kwargs)
    monkeypatch.setattr(graph_builder,'build_session_graph',build)
    class WS:
        def __init__(self,bounded):self._bounded_history=bounded;self._history_protocol=int(bounded);self.loaded=None
        async def send_text(self,text):
            frame=json.loads(text)
            if frame['type']=='session_loaded':self.loaded=frame['data']
    old,new=WS(False),WS(True)
    with server._sessions_lock:server._sessions['compacted-perf']={'id':'compacted-perf'}
    try:
        asyncio.run(ws_session.handle_load_session(old,{'session_id':'compacted-perf'}))
        asyncio.run(ws_session.handle_load_session(new,{'session_id':'compacted-perf'}))
        assert calls==[True,False]
        assert new.loaded['messages']==[wire_message(m) for m in old.loaded['messages']]
        assert any(m.get('kind')=='compaction' for m in new.loaded['messages'])
        assert old.loaded['graph'] and new.loaded['graph']==[]
    finally:
        close_snapshots(new)
        with server._sessions_lock:server._sessions.pop('compacted-perf',None)
