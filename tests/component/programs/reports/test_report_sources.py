"""Source discovery must preserve dates, privacy boundaries and original text."""
import json
from openprogram.programs._authoring.reports import sources


def test_discovery_keeps_current_originals_and_excludes_old_test_and_symlink(tmp_path, monkeypatch):
    rows = [
        {'id':'old', 'week':'2026-W36', 'audience':'tencent', 'text':'上周结果'},
        {'id':'current', 'week':'2026-W37', 'audience':'tencent', 'text':'本周尚未完成'},
        {'id':'synthetic_acceptance', 'week':'2026-W37', 'audience':'tencent', 'text':'虚构测试'},
    ]
    (tmp_path/'sources.json').write_text(json.dumps(rows))
    (tmp_path/'link.json').symlink_to(tmp_path/'sources.json')
    monkeypatch.setattr(sources, 'memory_candidates', lambda *a: (_ for _ in ()).throw(AssertionError('Do not mix memory into explicit Tencent evidence')))
    found = sources.collect('2026-W37', [str(tmp_path)])
    assert [x['text'] for x in found['materials']] == ['本周尚未完成']
    assert found['materials'][0]['source'].endswith('sources.json#current')


def test_personal_sources_require_selection_and_do_not_override_audience(tmp_path, monkeypatch):
    (tmp_path/'sources.json').write_text(json.dumps([
        {'id':'p', 'week':'2026-W37', 'audience':'personal', 'text':'个人科研记录'},
        {'id':'g', 'week':'2026-W37', 'audience':'group', 'text':'其他成员汇报'}]))
    monkeypatch.setattr(sources, 'memory_candidates', lambda *a: [])
    found = sources.collect('2026-W37', [str(tmp_path)])
    assert not found['materials']
    assert [x['text'] for x in found['candidates']] == ['个人科研记录']


def test_denied_root_is_not_read(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'validate_read_path', lambda p: 'denied')
    monkeypatch.setattr(sources, 'memory_candidates', lambda *a: [])
    found = sources.collect('2026-W37', [str(tmp_path)])
    assert not found['materials'] and not found['candidates']
    assert found['warnings']


def test_memory_discovery_uses_week_dates_and_excludes_pending_sources(tmp_path, monkeypatch):
    from openprogram.state import memory
    from openprogram.state.memory import store
    from openprogram.state.memory.retrieval import inspect
    seen = []
    monkeypatch.setattr(memory, 'is_enabled', lambda: True)
    monkeypatch.setattr(store, 'root', lambda: tmp_path)
    def search(root, query, **kw):
        seen.append(kw)
        return {'results':[
            {'path':'topics/opd.md','event_id':'current','content':'本周OPD实验','date':'2026-09-10'},
            {'path':'sources/untrusted.jsonl','content':'未经确认的消息','speaker_trusted':False},
            {'path':'topics/pending.md','content':'待审消息','trust_state':'pending'}]}
    monkeypatch.setattr(inspect, 'search', search)
    rows = sources.memory_candidates('2026-W37', '腾讯 OPD')
    assert len(rows) == 1 and rows[0]['source'] == 'memory:topics/opd.md#current'
    assert seen[0]['date_from'] == '2026-09-07'
    assert seen[0]['date_to'] == '2026-09-13'


def test_tencent_priority_is_independent_of_duplicate_audience_order(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'memory_candidates', lambda *a: [])
    for first in ('personal', 'group'):
        rows = [{'id':'other','week':'2026-W37','audience':first,'text':'同一段本周进展'},
                {'id':'tencent','week':'2026-W37','audience':'tencent','text':'同一段本周进展'}]
        for order in (rows, list(reversed(rows))):
            (tmp_path/'sources.json').write_text(json.dumps(order))
            found = sources.collect('2026-W37', [str(tmp_path)])
            assert len(found['materials']) == 1
            assert found['materials'][0]['source'].endswith('#tencent')
            assert not found['candidates']


def test_unrelated_json_request_does_not_abort_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'memory_candidates', lambda *a: [])
    (tmp_path/'unrelated.json').write_text(json.dumps({'request':'ordinary report request'}))
    (tmp_path/'sources.json').write_text(json.dumps([{'id':'real','week':'2026-W37','audience':'tencent','text':'本周真实进展'}]))
    assert len(sources.collect('2026-W37', [str(tmp_path)])['materials']) == 1


def test_coarse_memory_dates_cannot_be_relabelled_as_current_week(tmp_path, monkeypatch):
    from openprogram.state import memory
    from openprogram.state.memory import store
    from openprogram.state.memory.retrieval import inspect
    monkeypatch.setattr(memory, 'is_enabled', lambda: True)
    monkeypatch.setattr(store, 'root', lambda: tmp_path)
    monkeypatch.setattr(inspect, 'search', lambda *a, **k: {'results':[
        {'path':'topics/year.md','date':'2026','content':'往期结果'},
        {'path':'topics/month.md','date':'2026-09','content':'往期结果'},
        {'path':'topics/old.md','date':'2026-08-09','content':'往期结果'},
        {'path':'topics/current.md','date':'2026-09-10','dates':['2026-09-10'],'content':'本周结果'}]})
    rows = sources.memory_candidates('2026-W37', '腾讯')
    assert [x['text'] for x in rows] == ['本周结果']
    assert '2026-09-10' in rows[0]['source_dates']


def test_execution_checkpoint_and_derived_export_are_not_new_week_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'memory_candidates', lambda *a: [])
    row = {'id':'reference','week':'2026-W37','audience':'tencent','text':'用户粘贴的上周进展'}
    checkpoints = tmp_path/'checkpoints'
    checkpoints.mkdir()
    (checkpoints/'run.json').write_text(json.dumps({'kind':'tencent_model','request':{'materials':[row]}}))
    exported = tmp_path/'2026-W37'/'run'
    exported.mkdir(parents=True)
    (exported/'sources.json').write_text(json.dumps([{**row,'source':str(checkpoints/'run.json')+'#reference'}]))
    (exported/'summary.md').write_text('旧周报')
    found = sources.collect('2026-W37', [str(tmp_path)])
    assert found['materials'] == [] and found['candidates'] == []


def test_copied_derived_sources_without_summary_are_not_originals(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'memory_candidates', lambda *a: [])
    (tmp_path/'copied.json').write_text(json.dumps([{'id':'derived','week':'2026-W37',
        'audience':'tencent','text':'旧进度','source':'/reports/checkpoints/old.json#reference'}]))
    assert sources.collect('2026-W37', [str(tmp_path)])['materials'] == []


def test_direct_checkpoint_root_and_copied_checkpoint_are_not_originals(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'memory_candidates', lambda *a: [])
    checkpoints=tmp_path/'checkpoints'
    checkpoints.mkdir()
    state={'kind':'tencent_model','request':{'materials':[{'id':'r','week':'2026-W37','text':'旧进展'}]}}
    (checkpoints/'run.json').write_text(json.dumps(state))
    (tmp_path/'copied-state.json').write_text(json.dumps(state))
    assert sources.collect('2026-W37', [str(checkpoints)])['materials'] == []
    assert sources.collect('2026-W37', [str(tmp_path)])['materials'] == []


def test_exported_originals_without_reference_are_not_reimported(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'memory_candidates', lambda *a: [])
    (tmp_path/'sources.json').write_text(json.dumps([{'id':'r','week':'2026-W37','audience':'tencent','text':'旧进展'}]))
    (tmp_path/'summary.md').write_text('派生稿件')
    assert sources.collect('2026-W37', [str(tmp_path)])['materials'] == []


def test_memory_notes_are_newest_first_and_never_owner_words(tmp_path, monkeypatch):
    """Owner corrections come from the chat; a Topic paragraph is the writer's note."""
    from openprogram.state import memory
    from openprogram.state.memory import store
    from openprogram.state.memory.retrieval import inspect
    monkeypatch.setattr(memory, 'is_enabled', lambda: True)
    monkeypatch.setattr(store, 'root', lambda: tmp_path)
    monkeypatch.setattr(inspect, 'search', lambda *a, **k: {'results':[
        {'path':'topics/old.md','date':'2026-09-11','content':'早先计划'},
        {'path':'topics/note.md','date':'2026-09-13','content':'模型汇总'},
        {'path':'topics/new.md','date':'2026-09-12','content':'修订计划'}]})
    rows = sources.memory_candidates('2026-W37', '腾讯')
    assert [r['text'] for r in rows] == ['模型汇总','修订计划','早先计划']
    assert not any(r['trusted_owner'] for r in rows)


def test_detail_queries_are_bounded_deduplicated_and_keep_week(monkeypatch):
    import pytest
    calls = []
    anchor = {'id':'anchor','text':'本周动作'}
    def memory(week, query):
        calls.append((week, query))
        return [anchor, {'id':query,'text':'具体观察'}]
    monkeypatch.setattr(sources, 'memory_candidates', memory)
    found = sources.expand_memory_candidates('2026-W37', [' OPD ', 'OPD', 'Memory'], [anchor])
    assert calls == [('2026-W37','OPD'), ('2026-W37','Memory')]
    assert [r['id'] for r in found['candidates']] == ['anchor','OPD','Memory']
    with pytest.raises(ValueError):
        sources.expand_memory_candidates('2026-W37', ['a'] * 4, [])
    assert len(calls) == 2


def test_detail_failure_preserves_known_actions_and_context_limit(monkeypatch):
    def memory(week, query):
        if query == 'denied':
            raise OSError('read denied')
        return [{'id':str(i), 'text':'内容'*500} for i in range(30)]
    monkeypatch.setattr(sources, 'memory_candidates', memory)
    found = sources.expand_memory_candidates('2026-W37', ['denied','topic'], [{'id':'known','text':'本周明确动作'}])
    assert found['candidates'][0]['id'] == 'known'
    assert sum(len(json.dumps(r, ensure_ascii=False).encode()) for r in found['candidates']) <= 18000
    assert any('denied' in w for w in found['warnings'])


def test_collect_discovers_dated_owner_conversation_without_memory(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from openprogram.state.store import SessionStore
    from openprogram.runtime.context import session_db
    from openprogram.runtime.permissions.authority import owner_authority
    db = SessionStore(tmp_path / 'sessions')
    db.create_session('weekly', agent_id='main')
    predecessor = None
    for mid, role, day, content, authority in [
        ('old', 'user', '2026-09-06', '上周完成旧评估', owner_authority('owner/install/' + 'a' * 16)),
        ('draft', 'assistant', '2026-09-09', '模型拟定的成果', {}),
        ('unknown', 'user', '2026-09-10', '未归属消息', {}),
        ('current', 'user', '2026-09-11', '本周腾讯评估仍在运行，尚无最终结论', owner_authority('owner/install/' + 'a' * 16)),
    ]:
        db.append_message('weekly', {'id':mid, 'role':role, 'predecessor':predecessor,
            'timestamp':datetime.fromisoformat(day).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp(),
            'content':content, **authority})
        predecessor = mid
    monkeypatch.setattr(session_db, 'default_db', lambda: db)
    monkeypatch.setattr(sources, 'memory_candidates', lambda *a: [])
    found = sources.collect('2026-W37', [str(tmp_path / 'empty')])
    assert [r['text'] for r in found['candidates']] == ['本周腾讯评估仍在运行，尚无最终结论']
    row = found['candidates'][0]
    assert row['source'] == 'conversation:weekly#current'
    assert row['source_date'] == '2026-09-11' and row['trusted_owner'] is True


def test_conversation_discovery_honors_read_denial(tmp_path, monkeypatch):
    from openprogram.runtime.context import session_db
    from openprogram.state.store import SessionStore
    db = SessionStore(tmp_path / 'sessions')
    db.create_session('private', agent_id='main')
    monkeypatch.setattr(session_db, 'default_db', lambda: db)
    monkeypatch.setattr('openprogram.host.sandbox.validate_read_path', lambda p: 'denied')
    monkeypatch.setattr(db, 'get_messages', lambda *a, **k: (_ for _ in ()).throw(AssertionError('denied conversation read')))
    assert sources.conversation_candidates('2026-W37') == []


def test_copied_conversation_references_are_not_original_tencent_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'memory_candidates', lambda *a: [])
    monkeypatch.setattr(sources, 'conversation_candidates', lambda *a: [])
    (tmp_path/'copied.json').write_text(json.dumps([{'id':'derived','week':'2026-W37',
        'audience':'tencent','text':'旧进度','source':'conversation:old#message'}]))
    assert sources.collect('2026-W37', [str(tmp_path)])['materials'] == []


def test_real_sandbox_blocks_cached_conversation_and_public_tool(tmp_path, monkeypatch):
    import asyncio
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from openprogram.state.store import SessionStore
    from openprogram.runtime.context import session_db
    from openprogram.runtime.permissions.authority import owner_authority
    from openprogram.host.sandbox import SandboxPolicy
    from openprogram.programs.tools.knowledge.read_conversation import read_conversation
    db = SessionStore(tmp_path / 'sessions')
    db.create_session('private', agent_id='main')
    db.append_message('private', {'id':'original', 'role':'user', 'content':'PRIVATE_OWNER_REPORT_FACT',
        'timestamp':datetime(2026,9,30,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp(),
        **owner_authority('owner/install/' + 'a' * 16)})
    monkeypatch.setattr(session_db, 'default_db', lambda: db)
    monkeypatch.setattr(sources, 'memory_candidates', lambda *a: [])
    session_dir = db._session_dir('private')
    file_path = session_dir/'main.jsonl'
    assert file_path.is_file()
    for denied in (str(session_dir) + '/**', str(file_path)):
        monkeypatch.setattr('openprogram.host.sandbox.resolve_policy', lambda: SandboxPolicy(deny_read=(denied,)))
        result = asyncio.run(read_conversation.execute('read', {'session_id':'private'}, None, None))
        assert 'PRIVATE_OWNER_REPORT_FACT' not in str(result)
        assert 'denied' in str(result).lower()
        found = sources.collect('2026-W40', [str(tmp_path/'empty')])
        assert found['materials'] == [] and found['candidates'] == []
    monkeypatch.setattr('openprogram.host.sandbox.resolve_policy', lambda: SandboxPolicy())
    assert 'PRIVATE_OWNER_REPORT_FACT' in str(asyncio.run(read_conversation.execute('read', {'session_id':'private'}, None, None)))
    assert sources.collect('2026-W40', [str(tmp_path/'empty')])['candidates'][0]['text'] == 'PRIVATE_OWNER_REPORT_FACT'
