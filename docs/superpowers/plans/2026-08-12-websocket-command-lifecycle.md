# WebSocket Command Lifecycle Task Brief and Ledger

## Contract

- Approved design: `docs/reference/design/ui/websocket-command-lifecycle.html`.
- Base commit: `c1f10273`.
- Production file: `openprogram/webui/server.py`.
- Test file: `tests/unit/test_websocket_command_lifecycle.py`.
- Public RED cases: handler failure followed by ping on one socket; invalid JSON followed by ping; last focused connection disconnect releases legacy follow-up with `None`; another focused connection preserves wait and can answer; empty string remains a real answer.
- Error/privacy: client receives a stable generic `handler_error`; payload and exception message are not returned; local structured log retains traceback.
- Ownership/concurrency: dispatcher never clears action-owned reservation; only the last focused connection signals the session queue; queue removal remains owned by `_web_follow_up`.
- Compatibility: unknown-action frame and normal answers unchanged; socket write failure still terminates transport; runtime QuestionRegistry unchanged.
- Exclusions: global ask-user redesign, runtime.ask/ACP/channel protocols, send queue changes, Windows, credential store, orphan files, and other audit findings.

## Full gate manifest

```text
pytest -q tests/unit/test_websocket_command_lifecycle.py tests/unit/test_webui_head_mirror_and_run_guard.py tests/unit/test_turn_cancellation.py tests/meta_functions/test_follow_up.py
ruff check openprogram/webui/server.py tests/unit/test_websocket_command_lifecycle.py
python -m tools.docs_site.build
python -m tools.docs_site.checklinks
git diff --check
git status --short
```

## Ledger

| Evidence | Result |
|---|---|
| Base | `c1f10273` |
| Design | Pending commit |
| RED | Pending |
| GREEN | Pending |
| Affected verification | Pending |
| Specification review | Pending |
| Quality review | Pending |
| Full gate | Pending |
| Final implementation | Pending |
