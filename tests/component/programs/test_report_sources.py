"""Source discovery must preserve dates, privacy boundaries and original text."""
import json
from openprogram.programs.workflow import report_sources as sources


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
    from openprogram import memory
    from openprogram.memory import store
    from openprogram.memory.retrieval import inspect
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
    from openprogram import memory
    from openprogram.memory import store
    from openprogram.memory.retrieval import inspect
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
