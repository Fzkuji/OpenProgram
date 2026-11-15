"""File tools for SSH environments.

Each file tool checks :func:`remote_target` first; when the running
conversation lives in an SSH environment the tool delegates to the
matching function here, which works on the remote disk through
:class:`openprogram.host.environments.remote.RemoteFS`. Output formats match
the local tools so the model sees the same shapes either way.

Remote files are outside the host's turn checkpoints, so revert/undo of
a turn does not restore them; the remote read-before-edit gate still
applies.
"""
from __future__ import annotations

import os
import posixpath
import re
import shlex
import tempfile
from typing import Optional

from openprogram.host.environments.remote import (
    FRESH,
    RemoteFS,
    check_fresh,
    mark_seen,
    remote_fs,
    stale_message,
)


def remote_target() -> Optional[RemoteFS]:
    return remote_fs()


def resolve_remote(path: str | None) -> tuple[Optional[str], Optional[str]]:
    """``(absolute_path, error)``. Relative paths join the remote folder
    the conversation runs in; nothing touches the host filesystem."""
    if not path:
        from openprogram.state.worktree.context import current_worktree_path
        base = current_worktree_path()
        if not base:
            return None, "Error: no remote folder is bound; pass an absolute path"
        return base, None
    if "\x00" in path:
        return None, "Error: path contains a NUL character"
    if path.startswith("/"):
        return posixpath.normpath(path), None
    from openprogram.state.worktree.context import current_worktree_path
    base = current_worktree_path()
    if not base or not base.startswith("/"):
        return None, f"Error: path must be absolute, got {path!r}"
    return posixpath.normpath(posixpath.join(base, path)), None


def _emit(path: str, op: str) -> None:
    # emit_safe swallows its own failures.
    from openprogram.runtime.events import emit_safe
    emit_safe("file.changed", "tool", {"path": path, "op": op, "remote": True})


# ---- read ---------------------------------------------------------------


def read(fs: RemoteFS, file_path: str, offset: int, limit: int) -> str:
    from . import read as local

    path, err = resolve_remote(file_path)
    if err:
        return err
    assert path is not None
    try:
        data = fs.read_bytes(path)
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except IsADirectoryError:
        return f"Error: {path} is a directory, not a file"
    except (OSError, ConnectionError) as exc:
        return f"Error reading {path}: {exc}"
    mark_seen(fs.env, path, data)
    host = fs.env.ssh_target
    if path.lower().endswith(".pdf") or data[:5] == b"%PDF-":
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(data)
        try:
            out = local._read_pdf(tmp.name, offset, limit)
        finally:
            os.unlink(tmp.name)
        return out.replace(tmp.name, f"{host}:{path}")
    if (posixpath.splitext(path)[1].lower() in local._BINARY_EXTENSIONS
            or b"\0" in data[:8192]):
        import mimetypes
        media = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return f"# {host}:{path} (binary, {len(data)} bytes, {media})"
    lines = data.decode("utf-8", "replace").splitlines()
    return local.numbered_page(lines, offset, limit, shown=f"{host}:{path}", path=path)


# ---- write / edit ---------------------------------------------------------


def _gate(fs: RemoteFS, path: str) -> tuple[Optional[bytes], Optional[str]]:
    """Current bytes of an existing file plus a refusal when the agent
    never read it or it changed since. ``(None, None)`` = new file."""
    info = fs.stat(path)
    if not info.exists:
        return None, None
    if info.is_dir:
        return None, f"Error: {path} is a directory, not a file"
    data = fs.read_bytes(path)
    state = check_fresh(fs.env, path, data)
    if state != FRESH:
        return data, stale_message(f"{fs.env.ssh_target}:{path}", state)
    return data, None


def write(fs: RemoteFS, file_path: str, content: str) -> str:
    path, err = resolve_remote(file_path)
    if err:
        return err
    assert path is not None
    payload = content.encode("utf-8")
    try:
        _current, refusal = _gate(fs, path)
        if refusal:
            return refusal
        fs.write_bytes(path, payload)
    except (OSError, ConnectionError) as exc:
        return f"Error writing {path}: {exc}"
    mark_seen(fs.env, path, payload)
    _emit(path, "write")
    return f"Wrote {len(content)} bytes to {fs.env.ssh_target}:{path}"


def edit(fs: RemoteFS, file_path: str, old_string: str, new_string: str,
         replace_all: bool) -> str:
    path, err = resolve_remote(file_path)
    if err:
        return err
    assert path is not None
    if old_string == new_string:
        return "Error: old_string and new_string are identical — nothing to change"
    try:
        current, refusal = _gate(fs, path)
        if current is None and refusal is None:
            return f"Error: file not found: {path}"
        if refusal:
            return refusal
        assert current is not None
        text = current.decode("utf-8", "replace")
        count = text.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {path}"
        if count > 1 and not replace_all:
            return (f"Error: old_string occurs {count} times in {path}. "
                    "Add surrounding context to make it unique, or set replace_all=true.")
        new_text = (text.replace(old_string, new_string) if replace_all
                    else text.replace(old_string, new_string, 1))
        payload = new_text.encode("utf-8")
        fs.write_bytes(path, payload)
    except (OSError, ConnectionError) as exc:
        return f"Error editing {path}: {exc}"
    mark_seen(fs.env, path, payload)
    _emit(path, "edit")
    replaced = count if replace_all else 1
    return (f"Edited {fs.env.ssh_target}:{path} "
            f"({replaced} replacement{'s' if replaced != 1 else ''})")


def apply_patch(fs: RemoteFS, sections: list[tuple[str, str, list[str]]]) -> str:
    from .apply_patch import _add_content, _apply_hunks_to_text

    drafts: dict[str, Optional[str]] = {}
    originals: dict[str, Optional[bytes]] = {}
    errors: list[str] = []
    try:
        for op, raw_path, body in sections:
            if not raw_path.startswith("/"):
                errors.append(f"Error: path must be absolute: {raw_path}")
                continue
            path = posixpath.normpath(raw_path)
            if path not in originals:
                info = fs.stat(path)
                originals[path] = fs.read_bytes(path) if info.exists and not info.is_dir else None
                drafts[path] = (originals[path].decode("utf-8", "replace")
                                if originals[path] is not None else None)
            if op == "add":
                if drafts[path] is not None:
                    errors.append(f"Error: Add File target already exists: {path}")
                else:
                    drafts[path] = _add_content(body)
            elif op == "update":
                if drafts[path] is None:
                    errors.append(f"Error: Update File target not found: {path}")
                    continue
                original = originals[path]
                if original is not None:
                    state = check_fresh(fs.env, path, original)
                    if state != FRESH:
                        errors.append(stale_message(f"{fs.env.ssh_target}:{path}", state))
                        continue
                new_text, err, _ = _apply_hunks_to_text(drafts[path] or "", body, path)
                if err:
                    errors.append(err)
                else:
                    drafts[path] = new_text
            elif op == "delete":
                if drafts[path] is None:
                    errors.append(f"Error: Delete File target not found: {path}")
                else:
                    drafts[path] = None
            else:
                errors.append(f"Error: unknown op {op!r} for {path}")
    except (OSError, ConnectionError) as exc:
        return f"Error reading patch targets on {fs.env.ssh_target}: {exc}"
    if errors:
        return "\n".join(errors)
    results: list[str] = []
    try:
        for path, text in drafts.items():
            if text is None:
                if originals[path] is not None:
                    fs.delete(path)
                    _emit(path, "delete")
                    results.append(f"Deleted {fs.env.ssh_target}:{path}")
                continue
            payload = text.encode("utf-8")
            fs.write_bytes(path, payload)
            mark_seen(fs.env, path, payload)
            op = "add" if originals[path] is None else "update"
            _emit(path, op)
            results.append(f"{'Added' if op == 'add' else 'Updated'} {fs.env.ssh_target}:{path}")
    except (OSError, ConnectionError) as exc:
        results.append(f"Error: patch stopped part-way on {fs.env.ssh_target}: {exc}")
    return "\n".join(results)


# ---- discovery ----------------------------------------------------------


def _glob_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            close = pattern.find("]", i + 1)
            if close == -1:
                out.append(re.escape("["))
                i += 1
            else:
                body = pattern[i + 1:close].replace("\\", "\\\\")
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                i = close + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _find_files(fs: RemoteFS, root: str) -> tuple[list[tuple[float, str]], Optional[str]]:
    q = shlex.quote(root)
    prune = r"\( -name .git -o -name node_modules -o -name .venv \) -prune -o"
    command = (
        f"if find / -maxdepth 0 -printf '' >/dev/null 2>&1; then "
        f"find {q} {prune} -type f -printf '%T@\\t%p\\n'; "
        f"else find {q} {prune} -type f -exec stat -f '%m%t%N' {{}} +; fi"
    )
    code, out, err = fs.shell(command, timeout=120)
    if code not in (0, 1) and not out:
        return [], err.strip() or f"find exited {code}"
    rows: list[tuple[float, str]] = []
    for line in out.splitlines():
        stamp, _, path = line.partition("\t")
        try:
            rows.append((float(stamp), path))
        except ValueError:
            continue
    return rows, None


def glob(fs: RemoteFS, pattern: str, path: str | None) -> str:
    root, err = resolve_remote(path)
    if err:
        return err
    assert root is not None
    if pattern.startswith("/") or ".." in pattern.replace("\\", "/").split("/"):
        return (f"Error: pattern must be relative to `path` and must not "
                f"escape it, got {pattern!r}")
    if "**" in pattern and root in ("/", "/root", "/home"):
        return (f"Error: refusing recursive glob over {root!r}. Scope `path` "
                "to the specific project directory instead.")
    try:
        info = fs.stat(root)
        if not info.is_dir:
            return f"Error: not a directory: {root}"
        rows, error = _find_files(fs, root)
    except ConnectionError as exc:
        return f"Error: {exc}"
    if error:
        return f"Error: {error}"
    regex = _glob_regex(pattern)
    prefix = root.rstrip("/") + "/"
    matches = [(t, p) for t, p in rows
               if p.startswith(prefix) and regex.match(p[len(prefix):])]
    matches.sort(key=lambda row: row[0], reverse=True)
    paths = [p for _, p in matches]
    if not paths:
        return f"No matches for {pattern!r} under {fs.env.ssh_target}:{root}"
    if len(paths) > 500:
        return f"# {len(paths)} matches (showing 500 newest)\n" + "\n".join(paths[:500])
    return f"# {len(paths)} matches\n" + "\n".join(paths)


def grep(fs: RemoteFS, pattern: str, path: str | None, glob_filter: str | None,
         output_mode: str, case_insensitive: bool) -> str:
    root, err = resolve_remote(path)
    if err:
        return err
    assert root is not None
    rg = ["rg", "--no-heading", "--color=never"]
    gr = ["grep", "-rE", "--exclude-dir=.git", "--exclude-dir=node_modules"]
    if case_insensitive:
        rg.append("-i")
        gr.append("-i")
    if output_mode == "content":
        rg.append("-n")
        gr.append("-n")
    elif output_mode == "count":
        rg.append("-c")
        gr.append("-c")
    else:
        rg.append("-l")
        gr.append("-l")
    if glob_filter:
        rg += ["--glob", glob_filter]
        gr.append(f"--include={glob_filter}")
    rg += ["-e", pattern, "--", root]
    gr += ["-e", pattern, "--", root]
    rg_cmd = " ".join(shlex.quote(a) for a in rg)
    gr_cmd = " ".join(shlex.quote(a) for a in gr)
    command = (f"if command -v rg >/dev/null 2>&1; then {rg_cmd}; "
               f"else {gr_cmd}; fi | head -n 2000")
    try:
        code, out, errtext = fs.shell(command, timeout=120)
    except ConnectionError as exc:
        return f"Error: {exc}"
    lines = [ln for ln in out.splitlines() if ln]
    if output_mode == "count":
        lines = [ln for ln in lines if not ln.endswith(":0")]
    if not lines:
        if errtext.strip() and code not in (0, 1):
            return f"Error: {errtext.strip()}"
        return "No matches"
    return "\n".join(lines[:500])


def list_dir(fs: RemoteFS, path: str, show_hidden: bool) -> str:
    from .list import _fmt_size

    root, err = resolve_remote(path)
    if err:
        return err
    assert root is not None
    q = shlex.quote(root)
    command = (
        f"[ -d {q} ] || {{ echo 'not a directory' >&2; exit 2; }}; "
        f"cd {q} && for f in .* *; do "
        f"case \"$f\" in .|..) continue;; esac; "
        f"[ -e \"$f\" ] || [ -L \"$f\" ] || continue; "
        f"if [ -d \"$f\" ]; then printf 'd\\t%s\\n' \"$f\"; "
        f"else printf 'f\\t%s\\t%s\\n' \"$f\" \"$(wc -c < \"$f\" 2>/dev/null || echo -1)\"; fi; done"
    )
    try:
        code, out, errtext = fs.shell(command)
    except ConnectionError as exc:
        return f"Error: {exc}"
    if code == 2:
        return f"Error: not a directory: {root}"
    rows: list[str] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[1]
        if not show_hidden and name.startswith("."):
            continue
        if parts[0] == "d":
            rows.append(f"{name}/")
        else:
            size = parts[2].strip() if len(parts) > 2 else "-1"
            rows.append(f"{name}  {_fmt_size(int(size))}" if size.isdigit()
                        else f"{name}  [unreadable]")
    rows.sort(key=lambda r: r.rstrip("/").split("  ")[0])
    if not rows:
        return f"(empty{' — pass show_hidden=true to include dotfiles' if not show_hidden else ''})"
    return "\n".join(rows)


__all__ = [
    "apply_patch",
    "edit",
    "glob",
    "grep",
    "list_dir",
    "read",
    "remote_target",
    "resolve_remote",
    "write",
]
