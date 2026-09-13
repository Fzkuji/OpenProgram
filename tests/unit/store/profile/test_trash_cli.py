def _record_one_deletion(tmp_path, monkeypatch):
    from openprogram.sandbox.recoverable_delete import move_to_trash

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr("openprogram.paths._migration_checked", True)
    source = tmp_path / "deleted.txt"
    source.write_text("recover")
    trash_root = home / ".openprogram" / "trash" / "session-one" / "turn-two"
    return source, move_to_trash(source, trash_root=trash_root)


def test_trash_list_reports_records_and_capture_limit(tmp_path, monkeypatch, capsys):
    from openprogram.cli.commands.trash import _cmd_trash_list

    source, entry = _record_one_deletion(tmp_path, monkeypatch)

    assert _cmd_trash_list() == 0

    output = capsys.readouterr().out
    assert entry["id"] in output
    assert str(source) in output
    assert "available" in output
    assert "cron jobs, background tasks, remote backends" in output


def test_trash_restore_finds_record_without_overwriting(tmp_path, monkeypatch, capsys):
    from openprogram.cli.commands.trash import _cmd_trash_restore

    source, entry = _record_one_deletion(tmp_path, monkeypatch)

    assert _cmd_trash_restore(entry["id"]) == 0
    assert source.read_text() == "recover"
    assert str(source) in capsys.readouterr().out

    assert _cmd_trash_restore(entry["id"]) == 1
    assert "Refusing to overwrite existing path" in capsys.readouterr().err


def test_trash_parser_exposes_list_and_restore_verbs():
    from openprogram.cli import build_parser

    list_args = build_parser().parse_args(["trash", "list"])
    restore_args = build_parser().parse_args(["trash", "restore", "entry-id"])

    assert (list_args.command, list_args.trash_verb) == ("trash", "list")
    assert (restore_args.command, restore_args.trash_verb, restore_args.entry_id) == (
        "trash",
        "restore",
        "entry-id",
    )
