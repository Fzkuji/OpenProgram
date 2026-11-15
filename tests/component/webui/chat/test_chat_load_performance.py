"""A first read of a long chat sends its newest turns without drawing a hidden DAG."""
import asyncio
import json
import os
from pathlib import Path
import time

from openprogram.state.store import SessionStore
from openprogram.interfaces.server.ws_actions import session as ws_session


def test_cold_bounded_load_profile(tmp_path, monkeypatch):
    from openprogram.interfaces.server import server
    count = int(os.environ.get('PROFILE_CHAT_COUNT', '100'))
    root = tmp_path / 'sessions'
    store = SessionStore(root)
    store.create_session('perf-chat', 'main')
    for i in range(count):
        store.append_message('perf-chat', {'id':f'm{i}', 'role':'user' if i%2==0 else 'assistant',
            'content':f'Message {i}: '+'ordinary text '*20, 'predecessor':f'm{i-1}' if i else None})
    store.update_session('perf-chat', head_id=f'm{count-1}')
    store = SessionStore(root)
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda:store)
    monkeypatch.setattr(server, '_get_provider_info', lambda _sid=None:{})
    monkeypatch.setattr(server, '_is_run_active', lambda _sid:False)
    monkeypatch.setattr(server, 'refresh_context_stats', lambda _sid:None)
    timings={}
    from openprogram.state.store.session.graph import builder as graph_builder
    graph_calls=[]
    graph_original=graph_builder.build_session_graph
    def graph_probe(*args,**kwargs):
        graph_calls.append(kwargs)
        start=time.perf_counter()
        result=graph_original(*args,**kwargs)
        timings['build_session_graph']=time.perf_counter()-start
        return result
    monkeypatch.setattr(graph_builder,'build_session_graph',graph_probe)
    original=ws_session.load._session_io
    async def timed(func,*args,**kwargs):
        start=time.perf_counter()
        result=await original(func,*args,**kwargs)
        key=getattr(func,'__name__',str(func))
        timings[key]=timings.get(key,0)+time.perf_counter()-start
        return result
    monkeypatch.setattr(ws_session.load,'_session_io',timed)
    class WS:
        _history_protocol=3
        def __init__(self):self.loaded=None;self.first=None
        async def send_text(self,text):
            frame=json.loads(text)
            if frame['type']=='session_loaded':self.loaded=frame['data'];self.first=time.perf_counter()
    ws=WS()
    with server._sessions_lock:server._sessions['perf-chat']={'id':'perf-chat'}
    start=time.perf_counter()
    try:
        asyncio.run(ws_session.handle_load_session(ws,{'session_id':'perf-chat'}))
        rows=[row for turn in ws.loaded['turns'] for row in turn['rows']]
        assert rows[-1]['id']==f'm{count-1}'
        assert len(rows)<=50
        assert ws.loaded['graph']==[]
        if not os.environ.get('PROFILE_CHAT_ONLY'):
            assert graph_calls==[], "ordinary bounded Chat must not build an unused graph"
        record={'messages':count,'first_payload_seconds':ws.first-start,'phases_seconds':timings}
        print(json.dumps(record))
        if path:=os.environ.get('PROFILE_CHAT_OUTPUT'):
            Path(path).write_text(json.dumps(record,indent=2))
    finally:
        from openprogram.interfaces.server.transcript import publisher
        publisher.release(ws)
        with server._sessions_lock:server._sessions.pop('perf-chat',None)

