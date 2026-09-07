from openprogram.programs.gui_harness_bridge import install_gui_harness_web_use
from openprogram import system_access


def test_desktop_missing_access_stops_before_planning(monkeypatch):
    calls = []
    monkeypatch.setattr(system_access, 'report', lambda: {'platform': 'Darwin', 'capabilities': [{'id':'screen_recording','status':'not_granted','can_request':True}]})
    fn = install_gui_harness_web_use(lambda **kwargs: calls.append(kwargs) or {'status':'succeeded'})
    result = fn(task='Inspect my screen')
    assert calls == []
    assert result['reason_code'] == 'system_access_required'
    assert result['system_access'][0]['id'] == 'screen_recording'


def test_authorized_desktop_and_vm_keep_existing_execution(monkeypatch):
    calls = []
    monkeypatch.setattr(system_access, 'report', lambda: {'platform':'Darwin','capabilities':[{'id':'accessibility','status':'granted'}]})
    fn = install_gui_harness_web_use(lambda **kwargs: calls.append(kwargs) or {'status':'succeeded'})
    assert fn(task='Inspect my screen')['status'] == 'succeeded'
    monkeypatch.setattr(system_access, 'report', lambda: (_ for _ in ()).throw(AssertionError('VM must not inspect local permissions')))
    assert fn(task='Inspect remote', surface='vm', vm_url='http://vm:5000')['status'] == 'succeeded'
    assert len(calls) == 2
