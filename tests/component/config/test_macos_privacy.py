"""Installed bundle declarations needed for native consent, not OS grants."""
import importlib.util
import json
from pathlib import Path
import plistlib

ROOT = Path(__file__).resolve().parents[3]


def builder():
    spec = importlib.util.spec_from_file_location('runtime_builder', ROOT / 'scripts/release/build-macos-runtime-app.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_privacy_update_preserves_bundle_identity_and_existing_fields(tmp_path):
    module = builder()
    path = tmp_path / 'Info.plist'
    path.write_bytes(plistlib.dumps({'CFBundleIdentifier': 'test.runtime', 'Existing': True}))
    module.update_privacy_plist(path)
    result = plistlib.loads(path.read_bytes())
    assert result['CFBundleIdentifier'] == 'test.runtime'
    assert result['Existing'] is True
    assert result['NSAppleEventsUsageDescription']
    assert result['NSCalendarsFullAccessUsageDescription']
    assert result['NSRemindersFullAccessUsageDescription']
    assert result['NSMicrophoneUsageDescription']
    assert result['NSCameraUsageDescription']
    original = path.read_bytes()
    module.update_privacy_plist(path)
    assert path.read_bytes() == original


def test_outer_and_runtime_consent_descriptions_agree():
    from openprogram.host.system_access import validate_package_consistency
    package = json.loads((ROOT / 'apps/desktop/package.json').read_text())
    assert package['build']['mac']['extendInfo'] == builder().PRIVACY_USAGE
    entitlements = plistlib.loads((ROOT / 'apps/desktop/build/entitlements.mac.plist').read_bytes())
    assert entitlements['com.apple.security.automation.apple-events'] is True
    validate_package_consistency(
        package['build']['mac']['extendInfo'],
        {key for key, value in entitlements.items() if value is True and key.startswith('com.apple.')},
    )


def test_signed_bundles_can_reach_the_declared_camera_and_calendars():
    from openprogram.host.system_access import package_entitlements
    declared = package_entitlements()
    assert {'com.apple.security.device.camera', 'com.apple.security.personal-information.calendars'} <= declared
    for name in ('entitlements.mac.plist', 'entitlements.mac.helper.plist'):
        entitlements = plistlib.loads((ROOT / 'apps/desktop/build' / name).read_bytes())
        assert declared <= {key for key, value in entitlements.items() if value is True}, name
