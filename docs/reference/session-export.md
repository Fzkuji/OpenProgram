# Session export

Write a conversation out as a file you can keep, diff, or send to someone: Markdown for reading and reviewing, HTML for sharing a session with a person who does not run OpenProgram. Secrets are scrubbed on the way out.

## From the CLI

```bash
openprogram sessions export <session-id>                       # ./<session-id>.md
openprogram sessions export <session-id> --format html
openprogram sessions export <session-id> --output ~/review.md
```

| Option | Meaning | Default |
|-----|------|------|
| `--format` | `md` or `html` | `md` |
| `--output` | Where to write the file | `./<session-id>.<format>` |

Find session ids with `openprogram sessions list` or in the Web UI sidebar.

## From the Web UI

Right-click a conversation in the sidebar (or use the `⋯` button on the row), then pick **Export** and a format. The file downloads through your browser.

The same file is available directly at `GET /api/sessions/{session_id}/export?format=md|html`. It is an ordinary owner-authenticated endpoint, so it needs the same session cookie as the rest of the Web UI, and it answers with `Content-Disposition: attachment`.

## What lands in the file

One branch of the session — the conversational chain from the branch head backwards, the same walk the Web UI's transcript view uses. Each turn carries its role, its local timestamp, and its text. Underneath the turn that issued them come the tool calls: name, ok/failed status, arguments, and result.

Exporting reads the session's active head. Sessions that branched export the active branch, not every branch.

Tool results are capped at 4,000 characters and arguments at 1,000, with the cut marked inline (`… [+N chars truncated]`). One 2&nbsp;MB file read should not drown the reasoning around it. Turn text itself is not truncated.

The HTML export is one self-contained file: styles are inline, there are no scripts and no external requests, and it follows the reader's light/dark system setting through `prefers-color-scheme`. It opens in any browser, offline.

## Redaction

Every string in the export — turn text, tool arguments, tool results — passes through the same secret scrubber the provider recorder uses (`remove_secret_values` in `openprogram/providers/recording.py`). It removes the values of secret-named fields (`api_key`, `authorization`, `token`, `password`, …) and secret-shaped strings anywhere in free text: bearer tokens, `sk-` keys, credentials in URL query parameters or userinfo. Removed values are replaced with `[secret removed]`.

This is a safety net over known credential shapes, not a guarantee. A secret in an unusual format can still survive, so read an export before sending it somewhere public.

## Related

- [CLI commands](cli.md) — the full command surface.
- [API](API.md) — the rest of the HTTP endpoints.
