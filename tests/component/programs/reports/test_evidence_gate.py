"""Actual conversation reads bind report references to canonical visible nodes."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from openprogram.programs._authoring.reports.evidence_gate import EvidenceGate, MissingEvidence, UnsupportedReport


@pytest.fixture
def records(tmp_path, monkeypatch):
    from openprogram.state.store import SessionStore
    from openprogram.runtime.context import session_db
    from openprogram.runtime.permissions.authority import owner_authority
    db=SessionStore(tmp_path/'sessions')
    db.create_session('research',agent_id='main')
    owner=owner_authority('owner/install/'+'a'*16)
    before=None
    def add(mid, role, text, day='2026-09-30', **metadata):
        nonlocal before
        message={'id':mid,'role':role,'content':text,
                 'timestamp':datetime.fromisoformat(day).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp(), **metadata}
        if role!='tool':
            message['predecessor']=before
            before=mid
        db.append_message('research',message)
        return message
    add('owner','user','本周完成了实验，2 个测试通过；下周继续验证。',**owner)
    add('assistant','assistant','所有实验均已完成，844 个测试通过。')
    add('run','tool','2 passed',caller='assistant',function='bash',status='done',is_error=False,
        extra={'tool_use':{'arguments':{'command':'pytest'}}})
    monkeypatch.setattr(session_db,'default_db',lambda:db)
    yield db,add,owner
    db._save_index()


def read(gate, **args):
    return asyncio.run(gate.tool.execute('read',{'session_id':'research',**args},None,None))


def accept(monkeypatch, claims):
    from openprogram.programs._authoring.reports import evidence_gate
    calls=[]
    def verify(prompt, **kwargs):
        context=kwargs.get('context')
        calls.append('\n'.join([prompt,*(f'{k}: {v}' for k,v in (context.items() if context is not None else ()))]))
        return {'supported':True,'issues':[],'items':[
            {'field':field,'index':i,'text':text,'supported':True}
            for field,items in claims.items() for i,text in enumerate(items)]}
    monkeypatch.setattr(evidence_gate,'verifier',verify)
    return calls


def test_actual_read_exposes_only_original_sources_and_own_tool_metadata(records, monkeypatch):
    gate=EvidenceGate('2026-W40','personal_chat')
    receipt=read(gate)
    assert 'Bound original report sources' in str(receipt)
    assert {row['source'] for row in gate.rows.values()}=={'conversation:research#owner','conversation:research#run'}
    tool=next(row for row in gate.rows.values() if row['kind']=='tool_result')
    assert tool['text']=='2 passed' and tool['function']=='bash' and 'pytest' in tool['arguments']
    assert tool['source_date']=='2026-09-30' and tool['status']=='done' and tool['is_error'] is False
    claims={'progress_items':['本周完成实验，2 个测试通过']}
    calls=accept(monkeypatch,claims)
    gate.verify(claims,[{'field':'progress_items','index':0,'source':tool['id'],'quote':'2 passed'}])
    assert len(calls)==1 and gate.sources['source_refs'][0]['source']==tool['id']


@pytest.mark.parametrize('fault',['assistant','unknown','wrong_quote','missing_item','old_week','outside_range'])
def test_unread_or_unoriginal_references_never_verify(records, monkeypatch, fault):
    gate=EvidenceGate('2026-W40','personal_chat')
    if fault=='old_week':
        records[1]('old','user','旧周已完成',day='2026-09-20',**records[2])
    read(gate,start_turn=2 if fault=='outside_range' else 0,include_function_calls=False)
    calls=accept(monkeypatch,{'progress_items':['完成实验']})
    source=next(iter(gate.rows),'unread')
    ref={'field':'progress_items','index':0,'source':source,'quote':'本周完成了实验'}
    if fault in {'assistant','unknown','old_week'}:
        ref['source']=fault
    if fault=='wrong_quote':
        ref['quote']='844 个测试通过'
    if fault=='missing_item':
        ref['index']=1
    with pytest.raises((MissingEvidence,UnsupportedReport)):
        gate.verify({'progress_items':['完成实验']},[ref])
    assert calls==[]


def test_failed_and_pending_operational_results_keep_status_and_semantic_gate(records, monkeypatch):
    records[1]('failed','tool','Permission denied',caller='assistant',function='bash',is_error=True,status='failed')
    records[1]('pending','tool','Script running with job ID pending',caller='assistant',function='bash',is_error=False,status='running')
    gate=EvidenceGate('2026-W40','personal_chat')
    read(gate)
    failed=next(row for row in gate.rows.values() if row['kind']=='tool_failure')
    pending=next(row for row in gate.rows.values() if row['status']=='running')
    assert failed['is_error'] is True and failed['status']=='failed'
    assert pending['kind']=='tool_result' and pending['text']=='Script running with job ID pending'
    from openprogram.programs._authoring.reports import evidence_gate
    calls=[]
    def reject(prompt,**kwargs):
        calls.append(prompt)
        return {'supported':False,'issues':['only launch/failure, no completion'],'items':[]}
    monkeypatch.setattr(evidence_gate,'verifier',reject)
    with pytest.raises(UnsupportedReport,match='semantic'):
        gate.verify({'progress_items':['实验已全部完成']},[{'field':'progress_items','index':0,'source':pending['id'],'quote':pending['text']}])
    assert len(calls)==1 and 'running/job-ID' in calls[0]


def test_owner_request_is_not_completed_and_each_item_is_checked(records, monkeypatch):
    records[1]('request','user','请完成全部实验',**records[2])
    gate=EvidenceGate('2026-W40','personal_chat')
    read(gate)
    row=next(r for r in gate.rows.values() if r['source'].endswith('#request'))
    from openprogram.programs._authoring.reports import evidence_gate
    monkeypatch.setattr(evidence_gate,'verifier',lambda *a,**k:{'supported':False,'issues':['request is not completion'],'items':[]})
    with pytest.raises(UnsupportedReport):
        gate.verify({'progress_items':['全部实验已完成']},[{'field':'progress_items','index':0,'source':row['id'],'quote':row['text']}])


def test_read_denial_or_revocation_blocks_original_evidence(records, monkeypatch):
    from openprogram.host.sandbox import SandboxPolicy
    db=records[0]
    gate=EvidenceGate('2026-W40','personal_chat')
    read(gate)
    row=next(iter(gate.rows.values()))
    monkeypatch.setattr('openprogram.host.sandbox.resolve_policy',lambda:SandboxPolicy(deny_read=(str(db._session_dir('research'))+'/**',)))
    with pytest.raises(MissingEvidence):
        gate.verify({'progress_items':['完成实验']},[{'field':'progress_items','index':0,'source':row['id'],'quote':'本周完成了实验'}])
    denied=EvidenceGate('2026-W40','personal_chat')
    read(denied)
    assert denied.rows=={}


def test_canonical_receipt_mismatch_does_not_create_sources(records):
    from openprogram.models.providers.types import TextContent
    gate=EvidenceGate('2026-W40','personal_chat')
    original=gate.native
    async def changed(*args):
        result=await original.execute(*args)
        return result.model_copy(update={'content':[TextContent(type='text',text='fake transcript')]})
    gate.native=original.model_copy(update={'execute':changed})
    read(gate)
    assert gate.rows=={}


def test_current_supplied_owner_is_bound_to_actual_owner_request(records, monkeypatch):
    from openprogram.runtime.agent.turn_request import (
        set_turn_request,
        reset_turn_request,
    )
    db,_,owner=records
    node=next(m for m in db.get_messages('research') if m['id']=='owner')
    request=SimpleNamespace(session_id='research',user_msg_id='owner',user_text=node['content'],**owner)
    token=set_turn_request(request)
    try:
        gate=EvidenceGate('2026-W40','personal_chat')
        assert [r['text'] for r in gate.rows.values()]==[node['content']]
        request.speaker_kind='runtime'
        assert EvidenceGate('2026-W40','personal_chat').rows=={}
    finally:
        reset_turn_request(token)


def test_natural_previous_week_uses_resolved_week_and_original_dates(records, monkeypatch):
    records[1]('previous','user','上周完成实验',day='2026-09-23',**records[2])
    gate=EvidenceGate('为上周写一份草稿','personal_chat')
    read(gate)
    row=next(r for r in gate.rows.values() if r['source'].endswith('#previous'))
    claims={'progress_items':['完成实验']}
    calls=accept(monkeypatch,claims)
    gate.verify(claims,[{'field':'progress_items','index':0,'source':row['id'],'quote':'上周完成实验'}], '2026-W39')
    assert gate.sources['week']=='2026-W39' and len(calls)==1
    assert 'requested period' in calls[0]
    with pytest.raises(UnsupportedReport):
        gate.verify(claims,[{'field':'progress_items','index':0,'source':row['id'],'quote':'上周完成实验'}], '2026-W40')


def test_missing_status_remains_unknown_and_tool_own_week_is_not_caller_week(records):
    records[1]('unknown','tool','started',caller='assistant',function='bash',day='2026-09-23',status=None,is_error=None)
    gate=EvidenceGate('Summarize original sources','personal_chat')
    read(gate)
    row=next(r for r in gate.rows.values() if r['source'].endswith('#unknown'))
    assert row['status']=='unknown' and row['is_error'] is None and row['week']=='2026-W39'


def test_quotes_cannot_reference_truncated_original_suffix(records, monkeypatch):
    records[1]('long','user','a'*2500+'hidden completion',**records[2])
    gate=EvidenceGate('2026-W40','personal_chat')
    read(gate)
    row=next(r for r in gate.rows.values() if r['source'].endswith('#long'))
    assert len(row['text'])==2000
    with pytest.raises(UnsupportedReport):
        gate.verify({'progress_items':['completed']},[{'field':'progress_items','index':0,'source':row['id'],'quote':'hidden completion'}])
