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
