# Diagnostics bundle

`openprogram diagnostics` collects the information a maintainer needs to act on a bug report into a single zip, with credentials removed, so you can attach it to an issue instead of hunting for log files by hand.

```bash
openprogram diagnostics                          # ./openprogram-diagnostics-<date>.zip
openprogram diagnostics --output /tmp/report.zip  # write somewhere specific
```

The command prints the list of files it wrote so you can see what is in the bundle before you send it.

## What is in the bundle

| File | Contents |
|------|------|
| `version.json` | OpenProgram version, Python version and executable, platform and machine |
| `config.json` | Your `config.json` with every credential-shaped value replaced |
| `credentials.json` | Which providers have credentials and how many accounts each has — names and counts only |
| `environment.json` | The `openprogram doctor` checks, plus the web build state and state/log directory modes |
| `logs/worker.log` | Last 2000 lines of the worker log, redacted |
| `logs/runtime.log` | Last 2000 lines of the runtime log, redacted |
| `logs/ink-startup.log` | Last 2000 lines of the terminal UI startup log, redacted |
| `manifest.json` | Every file in the bundle, where it came from, and its size |

Logs that do not exist on your machine are simply absent from the bundle.

## What is removed

Redaction runs over every text file that enters the bundle, not only over the configuration, because log lines and tracebacks quote credentials too. Two layers apply.

**Key names.** Any configuration key whose name looks like a credential — `api_key`, `token`, `password`, `client_secret`, `authorization`, `cookie` and similar, matched as substrings so `openai_api_key` and `github_token` are caught — has its value replaced with `[secret removed]`. A matching key replaces its whole subtree, so nothing nested underneath can escape.

**Value shapes.** Recognisable credential formats are replaced wherever they appear in free text, including `sk-` provider keys, `Bearer` and `Basic` authorization headers, GitHub `ghp_` tokens, Slack `xox` tokens, AWS access key ids, Google `AIza` keys, JWTs, and credentials embedded in URLs. This is the layer that covers log lines, where there is no key name to inspect.

Credential files are never read. `credentials.json` is produced by listing the directory names under the auth store, so the contents of your stored tokens cannot reach the bundle even in redacted form.

## Before you share it

Redaction is thorough but it cannot know that a file path, a project name or a prompt fragment in a log line is sensitive to you. Open the zip and read it before attaching it to a public issue.

## Related

- [CLI reference](cli.md) — every subcommand and its flags
- [Configuration reference](config.md) — the keys that appear in the redacted `config.json`
