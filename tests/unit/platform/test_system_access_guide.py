"""The System Settings drag guide, copied from Codex Computer Use's accessory window."""
import sys
import threading

import pytest

from openprogram.host import system_access
from openprogram.host.system_access import guide


@pytest.mark.parametrize('capability', ['screen_recording', 'accessibility'])
def test_list_capabilities_get_the_guide(capability):
    assert guide.uses_guide(capability)
    assert system_access.capability_spec(capability).settings_pane in guide.LIST_PANES


@pytest.mark.parametrize('capability', [
    'calendar', 'reminders', 'microphone', 'camera', 'apple_events', 'file_read', 'file_write',
])
def test_dialog_and_operation_capabilities_get_no_guide(capability):
    assert not guide.uses_guide(capability)


def test_report_rows_say_which_capabilities_are_guided(monkeypatch):
    monkeypatch.setattr(system_access.platform, 'system', lambda: 'Linux')
    rows = {row['id']: row for row in system_access.report()['capabilities']}
    assert rows['screen_recording']['settings_guide'] is True
    assert rows['accessibility']['settings_guide'] is True
    assert rows['calendar']['settings_guide'] is False


def test_guide_text_names_the_pane_as_macos_does():
    title, detail = guide.guide_text('screen_recording')
    assert title == 'Drag OpenProgram to the list above to allow Screen Recording'
    assert 'switch' in detail and 'OpenProgram' in detail
    title, _ = guide.guide_text('accessibility', chinese=True)
    assert '辅助功能' in title


def test_quartz_rectangles_become_cocoa_rectangles():
    # A 600x400 window 100pt from the top of a 1000pt-high primary screen.
    assert guide.cg_to_cocoa((50, 100, 600, 400), 1000) == (50, 500, 600, 400)


def test_panel_sits_centred_just_below_the_settings_window():
    settings = (100.0, 500.0, 700.0, 450.0)
    visible = (0.0, 0.0, 1440.0, 875.0)
    x, y, width, height = guide.panel_frame(settings, visible, (440.0, 84.0))
    assert (width, height) == (440.0, 84.0)
    assert x == 100.0 + (700.0 - 440.0) / 2
    assert y + height == 500.0 - guide.PANEL_GAP


def test_panel_overlaps_the_bottom_edge_when_there_is_no_room_below():
    settings = (100.0, 40.0, 700.0, 800.0)
    visible = (0.0, 25.0, 1440.0, 850.0)
    x, y, _, _ = guide.panel_frame(settings, visible, (440.0, 84.0))
    assert y == 40.0 + guide.PANEL_INSET
    assert settings[1] < y < settings[1] + settings[3]


def test_panel_widens_to_its_text_within_bounds():
    assert guide.panel_width(100) == guide.PANEL_SIZE[0]
    assert guide.panel_width(380) == 104 + 380 + 40
    assert guide.panel_width(2000) == 640


def test_panel_stays_on_the_screen():
    settings = (-300.0, 400.0, 500.0, 400.0)
    visible = (0.0, 0.0, 1440.0, 875.0)
    x, _, _, _ = guide.panel_frame(settings, visible, (440.0, 84.0))
    assert x == 0.0
    settings = (1300.0, 400.0, 500.0, 400.0)
    x, _, width, _ = guide.panel_frame(settings, visible, (440.0, 84.0))
    assert x + width == 1440.0


def test_settings_window_is_the_front_main_window_of_system_settings():
    infos = [
        {'kCGWindowOwnerPID': 7, 'kCGWindowOwnerName': 'Finder', 'kCGWindowLayer': 0,
         'kCGWindowBounds': {'X': 0, 'Y': 0, 'Width': 900, 'Height': 700}},
        {'kCGWindowOwnerPID': 42, 'kCGWindowOwnerName': 'System Settings', 'kCGWindowLayer': 25,
         'kCGWindowBounds': {'X': 10, 'Y': 10, 'Width': 900, 'Height': 700}},
        {'kCGWindowOwnerPID': 42, 'kCGWindowOwnerName': 'System Settings', 'kCGWindowLayer': 0,
         'kCGWindowBounds': {'X': 20, 'Y': 20, 'Width': 120, 'Height': 40}},
        {'kCGWindowOwnerPID': 42, 'kCGWindowOwnerName': 'System Settings', 'kCGWindowLayer': 0,
         'kCGWindowBounds': {'X': 300, 'Y': 120, 'Width': 715, 'Height': 640}},
    ]
    assert guide.settings_window_bounds(infos, [42]) == (300.0, 120.0, 715.0, 640.0)
    assert guide.settings_window_bounds(infos[:1], [42]) is None
    # The owner name finds it when the bundle lookup found no process.
    assert guide.settings_window_bounds(infos, []) == (300.0, 120.0, 715.0, 640.0)


def test_off_macos_nothing_starts(monkeypatch):
    monkeypatch.setattr(guide.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(system_access, 'report', lambda: pytest.fail('no report off macOS'))
    assert guide.start(['screen_recording']) == []
    assert guide.start_for_wait('wait-linux', ['screen_recording']) == []


def test_unknown_capability_is_refused():
    with pytest.raises(ValueError):
        guide.start(['nonsense'])


def test_start_takes_only_missing_native_capabilities_in_order(monkeypatch):
    monkeypatch.setattr(guide.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(system_access, 'report', lambda: {'capabilities': [
        {'id': 'screen_recording', 'status': 'granted'},
        {'id': 'accessibility', 'status': 'not_granted'},
        {'id': 'calendar', 'status': 'not_granted'},
        {'id': 'apple_events', 'status': 'unknown'},
    ]})
    started = guide.start(['calendar', 'screen_recording', 'accessibility', 'apple_events', 'calendar'])
    assert started == ['calendar', 'accessibility']


def test_a_wait_starts_the_guide_once(monkeypatch):
    monkeypatch.setattr(guide.platform, 'system', lambda: 'Darwin')
    required = ['calendar', 'accessibility', 'screen_recording']
    assert guide.start_for_wait('wait-once', required) == ['accessibility', 'screen_recording']
    assert guide.start_for_wait('wait-once', required) == []
    assert guide.start_for_wait('wait-other', ['calendar']) == []


class _Process:
    def __init__(self, code):
        self.returncode = code

    def poll(self):
        return self.returncode

    def terminate(self):
        pass


def _sequence(monkeypatch, statuses, codes):
    calls = []
    monkeypatch.setattr(guide, '_status', lambda cap: statuses.get(cap, 'not_granted'))
    monkeypatch.setattr(system_access, 'request_access', lambda cap: calls.append(('request', cap)))
    monkeypatch.setattr(system_access, '_open_settings', lambda cap: calls.append(('open', cap)) or True)

    def launch(cap):
        calls.append(('guide', cap))
        return _Process(codes.pop(0))
    monkeypatch.setattr(guide, '_launch', launch)
    return calls


def test_sequence_asks_dialogs_and_guides_lists_in_order(monkeypatch):
    calls = _sequence(monkeypatch, {'camera': 'granted'}, [guide.EXIT_GRANTED, guide.EXIT_GRANTED])
    guide._run(['calendar', 'camera', 'screen_recording', 'accessibility'], threading.Event(), {})
    assert calls == [
        ('request', 'calendar'),
        ('open', 'screen_recording'), ('guide', 'screen_recording'),
        ('open', 'accessibility'), ('guide', 'accessibility'),
    ]


@pytest.mark.parametrize('code', [guide.EXIT_DISMISSED, guide.EXIT_SETTINGS_CLOSED, guide.EXIT_TIMEOUT])
def test_closing_a_guide_ends_the_sequence(monkeypatch, code):
    calls = _sequence(monkeypatch, {}, [code])
    guide._run(['screen_recording', 'accessibility'], threading.Event(), {})
    assert calls == [('open', 'screen_recording'), ('guide', 'screen_recording')]


def test_a_newer_guide_replaces_the_one_in_progress(monkeypatch, _no_system_settings_guide):
    real_begin = _no_system_settings_guide
    release = threading.Event()
    seen = []

    def run(capabilities, cancel, state):
        seen.append(capabilities)
        release.wait(5)
    monkeypatch.setattr(guide, '_run', run)
    first = real_begin(['screen_recording'])
    state = guide._current
    second = real_begin(['accessibility'])
    assert first == ['screen_recording'] and second == ['accessibility']
    assert state['cancel'].is_set()
    release.set()


def test_helper_runs_as_the_worker_executable(monkeypatch):
    commands = []

    class Popen:
        def __init__(self, command, **kwargs):
            commands.append((command, kwargs))
    monkeypatch.setattr(system_access, '_native_executor', lambda: '/runtime/OpenProgram')
    monkeypatch.setattr(guide.subprocess, 'Popen', Popen)
    guide._launch('accessibility')
    command, kwargs = commands[0]
    assert command[0] == '/runtime/OpenProgram'
    assert command[-1] == 'accessibility'
    assert 'openprogram.host.system_access.guide' in command[-2]
    assert kwargs['start_new_session'] is True


def test_helper_refuses_a_capability_without_a_list():
    with pytest.raises(SystemExit) as exited:
        guide.main(['calendar'] if sys.platform == 'darwin' else ['x', 'y'])
    assert exited.value.code == guide.EXIT_ERROR
