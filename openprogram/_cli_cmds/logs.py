"""``openprogram logs`` — inspect the various log files.

OpenProgram writes to three log files in two different state dirs:

* ``~/.openprogram/worker.log`` — backend webui + worker stdout/stderr
* ``~/.openprogram/logs/runtime.log`` — provider probe / detect /
  agent runtime diagnostic chatter (silent on the terminal,
  always written here so it's findable)
* ``~/.openprogram/logs/ink-startup.log`` — Python startup chatter
  during Ink TUI launch (warnings, provider probes that ran
  while stdio was dup2'd into this file)

Verbs:

  openprogram logs                 # default: tail worker.log
  openprogram logs list            # show all log files with size + mtime
  openprogram logs path [name]     # print absolute path (for piping)
  openprogram logs tail [name] [-n N] [-f]   # tail (optionally follow)

``name`` is fuzzy: ``worker`` / ``runtime`` / ``ink`` all match.
Cross-platform — no tail / less / head dependency; written in
pure Python.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional


def _log_targets() -> list[tuple[str, Path]]:
    """Return ``[(name, path), ...]`` for every known log.

    All under the canonical state dir (``~/.openprogram/``). worker.log
    sits at the state-dir root; runtime/ink logs live in ``logs/``.
    """
    from openprogram.paths import get_state_dir
    state = get_state_dir()
    return [
        ("worker", state / "worker.log"),
        ("runtime", state / "logs" / "runtime.log"),
        ("ink-startup", state / "logs" / "ink-startup.log"),
    ]


def _resolve(name: Optional[str]) -> Optional[Path]:
    """Fuzzy-match ``name`` to a known log. ``None`` → worker.log
    (the most useful default for "what's the server doing?")."""
    targets = _log_targets()
    if not name:
        return targets[0][1]
    needle = name.lower()
    for tag, path in targets:
        if tag == needle or tag.startswith(needle) or needle in tag:
            return path
    return None


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if isinstance(n, float) else f"{n}{unit}"
        n = n / 1024  # type: ignore[assignment]
    return f"{n:.1f}TB"


def _human_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _cmd_logs_list() -> int:
    rows = []
    now = time.time()
    for tag, path in _log_targets():
        if path.exists():
            stat = path.stat()
            rows.append((tag, str(path), _human_size(stat.st_size),
                         _human_age(now - stat.st_mtime)))
        else:
            rows.append((tag, str(path), "(missing)", ""))
    if not rows:
        print("No logs known.")
        return 0
    name_w = max(len(r[0]) for r in rows)
    size_w = max(len(r[2]) for r in rows)
    age_w = max(len(r[3]) for r in rows)
    print(f"{'name':<{name_w}}  {'size':>{size_w}}  {'updated':<{age_w}}  path")
    for name, path, size, age in rows:
        print(f"{name:<{name_w}}  {size:>{size_w}}  {age:<{age_w}}  {path}")
    return 0


def _cmd_logs_path(name: Optional[str]) -> int:
    path = _resolve(name)
    if path is None:
        print(f"Unknown log: {name!r}. Try `openprogram logs list`.",
              file=sys.stderr)
        return 2
    print(str(path))
    return 0


def _cmd_logs_tail(name: Optional[str], lines: int, follow: bool) -> int:
    """Last ``lines`` lines, then optionally follow appends."""
    path = _resolve(name)
    if path is None:
        print(f"Unknown log: {name!r}. Try `openprogram logs list`.",
              file=sys.stderr)
        return 2
    if not path.exists():
        print(f"Log file does not exist yet: {path}", file=sys.stderr)
        return 1

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            # Tail: read all, keep last N lines. For huge files we
            # could seek to EOF and back, but log files here are
            # rotated at ~MB scale so reading all is fine and simpler.
            all_lines = f.readlines()
            tail_lines = all_lines[-lines:] if lines > 0 else all_lines
            sys.stdout.write("".join(tail_lines))
            if not follow:
                return 0
            # Follow: poll for appends. inotify / kqueue would be
            # platform-specific; polling is portable + good enough
            # for a log viewer (~500ms latency).
            f.seek(0, 2)  # to EOF
            try:
                while True:
                    line = f.readline()
                    if line:
                        sys.stdout.write(line)
                        sys.stdout.flush()
                    else:
                        time.sleep(0.5)
            except KeyboardInterrupt:
                return 0
    except OSError as e:
        print(f"Failed to read {path}: {e}", file=sys.stderr)
        return 1


__all__ = ["_cmd_logs_list", "_cmd_logs_path", "_cmd_logs_tail"]
