"""Exercise the Desktop package manifest and refresh copy loop."""
from pathlib import Path
import json

DESKTOP = Path(__file__).resolve().parents[3] / "apps" / "desktop"


def test_refresh_copies_declared_nested_desktop_modules(tmp_path):
    import os
    import subprocess
    import sys

    refresh = (DESKTOP.parents[1] / "scripts/refresh-local-app.sh").read_text(encoding="utf-8")
    selection = refresh.split('desktop_files="$(' , 1)[1].split(')"', 1)[0]
    start = refresh.index("  while IFS= read -r desktop_file; do")
    end = refresh.index('  done <<<"$desktop_files"', start) + len('  done <<<"$desktop_files"')
    source = tmp_path / "repo/apps/desktop"
    (source / "main").mkdir(parents=True)
    (source / "main.js").write_text("entry", encoding="utf-8")
    (source / "main/actions.js").write_text("nested module", encoding="utf-8")
    (source / "package.json").write_text(json.dumps({"build": {"files": ["main.js", "main/actions.js"]}}), encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()
    env = {**os.environ, "repo_root": str(tmp_path / "repo"), "desktop_stage": str(stage), "local_python": sys.executable}
    subprocess.run(["bash", "-ec", 'desktop_files="$(' + selection + ')"\n' + refresh[start:end]], env=env, check=True, capture_output=True, text=True)
    assert (stage / "main/actions.js").read_text(encoding="utf-8") == "nested module"


def test_refresh_preserves_and_repairs_unpacked_permissions(tmp_path):
    import os
    import subprocess
    import sys

    root = DESKTOP.parents[1]
    asar = root / "node_modules/@electron/asar/bin/asar.js"
    import shutil
    import pytest

    if os.name == "nt" or not shutil.which("node") or not asar.is_file():
        pytest.skip("Requires POSIX, Node and npm ci --ignore-scripts")
    source = tmp_path / "source"
    helper = source / "node_modules/node-pty/prebuilds/darwin-arm64/spawn-helper"
    helper.parent.mkdir(parents=True)
    helper.write_text("#!/bin/sh\nprintf helper-ok")
    helper.chmod(0o755)
    data = helper.with_name("data.json")
    data.write_text("{}")
    data.chmod(0o640)
    installed = tmp_path / "installed.asar"
    subprocess.run(["node", str(asar), "pack", str(source), str(installed), "--unpack-dir", "node_modules/node-pty"], check=True)
    refresh = (root / "scripts/refresh-local-app.sh").read_text()
    start = refresh.index('  node "$asar_cli" extract')
    end = refresh.index('  while IFS= read -r desktop_file', start)
    installed_helper = Path(str(installed) + ".unpacked") / helper.relative_to(source)
    for damaged in (False, True):
        installed_helper.chmod(0o644 if damaged else 0o755)
        stage = tmp_path / f"stage-{damaged}"
        env = {**os.environ, "HOME": str(tmp_path), "repo_root": str(root), "asar_cli": str(asar), "installed_asar": str(installed), "desktop_stage": str(stage), "local_python": sys.executable}
        subprocess.run(["bash", "-ec", refresh[start:end]], env=env, check=True)
        repacked = tmp_path / f"repacked-{damaged}.asar"
        subprocess.run(["node", str(asar), "pack", str(stage), str(repacked), "--unpack-dir", "node_modules/node-pty"], check=True)
        unpacked = Path(str(repacked) + ".unpacked")
        result = subprocess.run([str(unpacked / helper.relative_to(source))], capture_output=True, text=True, check=True)
        assert result.stdout == "helper-ok"
        assert (unpacked / data.relative_to(source)).stat().st_mode & 0o777 == 0o640
