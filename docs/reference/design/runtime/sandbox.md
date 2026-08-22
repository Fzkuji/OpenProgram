# Sandbox

The sandbox is the **host-native process isolation layer**: Seatbelt on macOS and bubblewrap on Linux constrain the filesystem, process view, environment, and network of a child process. The fixed `owner`/`paired` authority-tier table, permission rules, and exact owner approval decide whether an operation may be attempted; `SandboxPolicy` decides what an admitted process can access; hard constraints run before either layer ([`permission-model.md`](permission-model.md) §1.1). An approved retry remains inside the OS sandbox and cannot remove credential filtering or the hard floor.

A rendered walkthrough of the same material lives at [`sandbox-architecture.html`](sandbox-architecture.html).

**This document has three layers.** [Part I](#part-i--what-we-do-today) is the current implementation, measured. [Part II](#part-ii--what-the-reference-implementations-do) is what all eight harnesses under `references/` do, including the ones that deliberately have no sandbox. [Part III](#part-iii--implementation-decisions-and-record) records the adopted design, its reference precedents, and the implementation sequence.

---

## Part I — What we do today

The policy implementation is `openprogram/sandbox/__init__.py`. Its public names include `SandboxPolicy` (a frozen dataclass), `resolve_policy()`, `is_available()` / `unavailable_reason()`, `child_env(policy)`, `validate_write_path()`, and `wrap_command(command, cwd, policy) -> (args, shell)`. `openprogram/backend/local.py::_invocation` is the shared command boundary used by bash, process, local `execute_code`, cron direct jobs, the memory writer's MCP `shell`, and one-shot MCP startup. Spawned agentic processes receive an explicit policy snapshot. The direct `write`, `edit`, and `apply_patch` tools apply the same writable-root and hard-floor checks without converting file operations into shell commands.

The selected local backend is intentionally host-native so commands use the host's real Git, Python, Conda, npm, compiler, and project environment. Docker is neither the local sandbox implementation nor an automatic fallback. The existing Docker and SSH execution backends declare their own container or remote-host boundary; a separate Docker sandbox backend is deferred until a concrete requirement needs an explicitly selected isolated Linux environment.

### 1. The boundary

Four directions, and they are not symmetric. Reads are open except the credential globs, writes are confined to the working directory, execution is unrestricted, and the network is off on both platforms. Everything in this section is measured on the shipped code.

#### 1.1 macOS — Seatbelt

`wrap_command` returns `/usr/bin/sandbox-exec -p <profile> /bin/bash -c <command>`. The profile is generated inline by `_seatbelt_profile()`:

| Resource | Policy |
|---|---|
| Fallback | `(deny default)` |
| File read | `(allow file-read* (subpath "/"))`, then a `deny file-read*` regex per deny-read glob |
| File write | cwd, extra `writable_roots`, the current process `TMPDIR`, `/private/tmp`, `/tmp`, then a `deny file-write*` regex per deny-write glob |
| Deletion of a denied path | `deny file-write-unlink` on every deny-read glob, so a blocked path cannot be probed by trying to remove it |
| Process exec | `(allow process-exec)`, unrestricted; children inherit the profile |
| fork | allowed |
| Signals, process info | `(target same-sandbox)` only |
| POSIX semaphores and shared memory | allowed, for Python multiprocessing |
| Character devices | read, write and ioctl on `/dev/null`, `/dev/zero`, `/dev/random`, `/dev/urandom`, `/dev/tty`, each with `require-all` plus `vnode-type CHARACTER-DEVICE` |
| sysctl | hardware-name prefix plus `kern.hostname`, `kern.osrelease`, `kern.ostype`, and `kern.version` |
| Mach IPC | no general `mach-lookup` grant |
| Network | no rule unless `sandbox.network` is on, so `(deny default)` blocks both directions |

The working directory is escaped before it is interpolated into the profile, and the deny globs are compiled to anchored regexes. Two details of Seatbelt's regex dialect are load-bearing: `(?:…)` never matches, so a non-capturing group turns a deny rule into a silent no-op, and the engine matches the symlink-resolved path, so each glob is emitted twice when its static prefix resolves elsewhere.

#### 1.2 Linux — bubblewrap

```
bwrap --new-session --die-with-parent --unshare-pid --unshare-ipc --unshare-uts
      --cap-drop ALL --ro-bind / / --proc /proc --dev /dev [--unshare-net]
      --tmpfs /tmp --bind <cwd> <cwd> [deny mounts] -- /bin/bash -c <command>
```

| Resource | Policy |
|---|---|
| File read | `--ro-bind / /`, minus the deny-read mounts below |
| File write | cwd and extra `writable_roots` via `--bind`, plus a throwaway tmpfs at `/tmp` |
| Deny-read directory | `--perms 0000 --tmpfs <dir>`; `--cap-drop ALL` removes `DAC_OVERRIDE`, so the mode holds even when the child is root in a container |
| Deny-read file | `--ro-bind /dev/null <file>` |
| Process exec | unrestricted, any binary anywhere |
| Network | `--unshare-net` unless `sandbox.network` is on |
| PID namespace | unshared |
| IPC, UTS namespaces | unshared |
| Capabilities | all dropped |
| Terminal | `--new-session`, which bubblewrap documents as the TIOCSTI injection guard |
| Lifetime | `--die-with-parent` |
| Syscalls | no seccomp filter |
| Environment | filtered by the caller, see §1.4 |

Mount order matters and is not cosmetic. `--tmpfs /tmp` is emitted **before** the cwd bind; the other order lets the tmpfs cover a working directory under `/tmp`, and the workspace disappears inside the sandbox while the host copy is untouched. Every `tempfile`-created staging directory lands there, the memory writer's included. Deny mounts are skipped for paths that do not exist, because the root is bound read-only and bubblewrap cannot create a mount point in it — the attempt fails the whole invocation with `Can't create file at <path>: Read-only file system`.

`--unshare-user` is deliberately absent. The non-setuid build already creates a user namespace on its own, so the flag adds nothing, and the setuid build does not support it at all.

Availability is an execution check, not only a PATH check. The first Linux check for each `bwrap` executable starts `/bin/true` with the PID, IPC, UTS, network, mount and capability restrictions required by the policy. A host that has `bwrap` installed but forbids unprivileged user namespaces is therefore treated as unavailable and follows `sandbox.unavailable_policy`; it is never reported as sandboxed after an unsandboxed fallback.

#### 1.3 What the deny list holds

The list ships loaded rather than empty: `~/.ssh/**`, `~/.aws/**`, `~/.gnupg/**`, `~/.openprogram/auth/**`, `~/.claude.json`, `~/.claude/.credentials.json`, `~/.config/gh/**`, `~/.netrc`, `~/Library/Keychains/**`, `**/.env`. Measured with the sandbox on, the concrete credential paths fail with `Operation not permitted` on macOS and `Permission denied` on Linux, and `rm -f ~/.ssh/id_ed25519` fails instead of revealing whether the file exists. The middle-wildcard `**/.env` rule is enforceable only by the macOS regex profile. Linux users must name an exact path or a concrete directory deny such as `/absolute/path/to/secrets/**`; bubblewrap cannot implement a filesystem-wide middle-wildcard match.

Everything outside the list stays readable. That is the deliberate posture, not an oversight: the whole-disk read is what lets a command inspect the system it is working on, and the loop is closed on the paths that carry credentials rather than on reading in general.

#### 1.4 The child environment

A sandboxed child gets an allowlist — `PATH`, `HOME`, `SHELL`, `USER`, `LOGNAME`, `TERM`, `TMPDIR`, `TMP`, `TEMP`, `TZ`, `PWD`, `OLDPWD`, `LANG`, `LANGUAGE`, `COLUMNS`, `LINES` and `LC_*` — plus any name in `sandbox.pass_env` whose name does not itself read as a credential. Measured: a 164-character `OPENAI_API_KEY` in the parent arrives as an empty string in the child, and `env | grep -iE '(key|token|secret|password)='` returns nothing.

An allowlist is chosen over a denylist derived from the provider registry for one reason: a provider added tomorrow is dropped without anyone updating anything, whereas a derived list has to be rebuilt whenever the registry grows. The credential-name pattern is kept as a floor under `sandbox.pass_env`, so the escape hatch cannot hand a key to every command by accident.

On Linux the environment filter alone would not be enough. Without `--unshare-pid`, `/proc/<agent_pid>/environ` hands back the key that was just removed from the child. With it, the sandboxed process sees four PIDs, `cat /proc/<host pid>/environ` reports "No such file or directory", and `kill -9 <host pid>` reports "No such process" while the host process stays alive.

#### 1.5 Known behavior and platform limits

- `ps` and `top` do not run on macOS. They are setuid binaries and Seatbelt refuses to exec those into a sandbox regardless of the exec policy, so this is a platform limit rather than a configuration choice. Every reference implementation that uses Seatbelt has it too.
- Git hooks and repository config are writable inside the working directory. Owners can add them to `sandbox.deny_write`, but they are not denied by default: measured, denying `.git/hooks/**` makes `git init` and `git clone` fail because both write that directory. This is a documented compatibility choice.
- Sandbox denials that carry platform error text produce a structured `sandbox.violation` event. Silent service denials such as `pbpaste` remain conservative failures and do not offer escalation.

### 2. The switch

The policy is read from `sandbox.*` in `~/.openprogram/config.json` at the moment a command is wrapped. New installations default to `workspace-write`; an existing explicit `danger-full-access` remains unsandboxed. Seven keys are registered in `openprogram/config_schema.py::SETTINGS`, so `openprogram config`, the setup wizard, the TUI settings screen and the web settings page all render them:

| Key | Meaning | Default |
|---|---|---|
| `sandbox.mode` | `danger-full-access` or `workspace-write` | `workspace-write` |
| `sandbox.writable_roots` | directories writable besides the working directory | `[]` |
| `sandbox.deny_read` | globs no sandboxed command may read | the credential list in §1.3 |
| `sandbox.deny_write` | globs no sandboxed command may write | `[]`, plus the always-on agentics directory |
| `sandbox.network` | network inside the sandbox | `false` |
| `sandbox.pass_env` | extra environment names to pass through | `[]` |
| `sandbox.unavailable_policy` | `refuse` or `warn` when the platform backend is missing or cannot create its required isolation | `refuse` |

`/sandbox` in the CLI REPL and in the web UI both write `sandbox.mode` through `set_setting`, so the toggle is persistent rather than per-session.

**The switch is persisted configuration.** It used to be a `ContextVar`, which is lost at every boundary that starts a fresh context. Three of those boundaries are on real call paths: the web UI sets the flag in the websocket's asyncio task while the agent turn runs in a bare `threading.Thread`, `openprogram/agent/process_runner.py` uses `mp.get_context("spawn")` which does not carry context variables, and nested CLIs are separate processes entirely. Adding `copy_context()` at each hand-off is not equivalent: a spawned sub-agent can carry the flag into its worker thread that way and still return to the default on the measured follow-up thread. Per-chain accounting state remains in context variables and is rebound at each thread entry; installation policy does not. Measured after the change, the web worker thread runs sandboxed and sees an empty `OPENAI_API_KEY`, and the spawned subprocess does too.

**Only a local interactive owner can request one exact retry with relaxed configurable restrictions.** The rerun still uses the OS sandbox, preserves credential filtering and the non-configurable agentics write prohibition, and never applies to cron, subagents or shared channels. `permission_mode="bypass"` cannot remove the hard floor or the sandbox.

**An unavailable backend refuses by default.** `_invocation` raises `SandboxUnavailable` when `sandbox.mode` is on, the platform backend is missing or its required isolation probe fails, and `sandbox.unavailable_policy` is `refuse`; `LocalBackend.run` turns that into a failed `RunResult` whose message names the reason and the explicit unsafe alternatives. `warn` restores the old behaviour of running unprotected, with a log line.

Granularity is still one setting for the whole installation: not per agent, not per tool, not per command. `wrap_command` takes an explicit policy, so a caller that has one can pass it, but nothing supplies a different policy per call site yet.

### 3. Coverage

The pre-repair audit found 25 execution surfaces: two already used the shared sandbox policy and 23 did not have a uniform classification. That count is retained as a historical baseline. The complete U01–U23 ledger, including source class, enforcement boundary, retained exceptions, and acceptance criteria, is in [the architecture document](sandbox-architecture.html#cover).

The current classification is:

| Execution class | Current boundary |
|---|---|
| Local model-supplied commands | bash, process, local `execute_code`, cron direct jobs, the memory-writer MCP shell, and one-shot MCP startup use `LocalBackend._invocation`; unattended and writer paths force the sandbox |
| Spawned agentic functions | `SandboxPolicy` and authority fields are explicitly serialized into the child; missing execution authority denies host side effects |
| Direct file tools | `write`, `edit`, and `apply_patch` enforce normalized writable roots and the non-configurable protected paths before changing files |
| Cron prompt and direct jobs | creation and management require schedule capabilities; a signed immutable execution spec fixes principal, scope, cwd, content, policy, and hash; firing cannot ask for or obtain broader approval |
| Nested Claude Code | built-in command and file side-effect tools are disabled; managed MCP replacements enforce staged-workspace paths, audit, and a forced OS sandbox for shell commands |
| Dynamic program import | auto-import accepts owner-recorded sources only; model-writable directories are not eligible import sources |
| Docker and SSH backends | the configured container or remote host is the execution boundary; tool rules, authority, approval, and audit still apply, but the host-native sandbox is not claimed |
| Fixed-argv and owner-admin paths | deterministic Git/store operations, configured plugins/MCP/hooks, and explicit install or upgrade commands retain their documented boundary and are not misreported as model shell commands |

`permission_mode="bypass"` no longer precedes security enforcement. Hard constraints and capability checks execute first, and subagents are non-interactive, inherit no more than the caller's scope, and cannot request escalation. The ignored provider keyword `sandbox="read-only"` is not counted as protection; process isolation is supplied by the explicit policy snapshot and the boundaries above.

---

## Part II — What the reference implementations do

Eight harnesses live under `references/`. All eight are covered below, including the four that ship no sandbox at all, because declining to isolate is itself a design position with a stated substitute.

Two notes on counting. `pi-ai` is not an independent harness: it is a read-only verbatim subset of the same upstream as `pi-mono` (`references/pi-ai/README.md:1-6`, both remotes point at `badlogic/pi-mono`), containing only the provider and protocol layer. And two of the four OS-level sandboxes are the same code: `claude-code` and `pi-mono`'s sandbox extension both call `@anthropic-ai/sandbox-runtime`, so there are two independent syscall-level implementations in the set, not three.

### 4. Four postures

| Harness | OS-level sandbox | Mechanism | Where the model's command runs | On by default |
|---|---|---|---|---|
| `claude-code` | yes | Seatbelt on macOS, bubblewrap plus a seccomp helper on Linux, in the external `@anthropic-ai/sandbox-runtime` package | host, wrapped | no (`sandbox.enabled` false) |
| `codex-cli` | yes | Seatbelt SBPL and bubblewrap plus seccomp, in-tree under `codex-rs/` | host, wrapped | **yes**, `read-only` |
| `openclaw` | yes, at a coarser grain | **Docker container**, one per agent by default, via a pluggable backend registry (`docker` / `ssh` / plugin-supplied) | **inside a container**, or on a remote host | no (`sandbox.mode` `"off"`) |
| `pi-mono` | only as an example extension | `@anthropic-ai/sandbox-runtime`, wired in by replacing the `bash` tool implementation | host, unwrapped unless the extension is installed | n/a in core; the extension defaults to enabled |
| `hermes-agent` | no on the default path, optional backends | `TERMINAL_ENV` selects `local` (default), `ssh`, or a container/remote backend (`docker`, `singularity`, `modal`, `daytona`, `vercel_sandbox`) | host by default | n/a; default is host |
| `opencode` | **no, documented as a non-goal** | — | host | — |
| `weclaw` | **no, and it disables the sandbox of the agent it wraps** | — | host, through a spawned `claude` / `codex` | — |
| `pi-ai` | n/a | no execution surface at all | — | — |
| **OpenProgram** | yes | Seatbelt and bubblewrap, in-tree | host, wrapped | yes (`sandbox.mode` `workspace-write`) |

**`claude-code`** — verified against the installed 2.1.226 binary, since `references/claude-code-leaked/src/utils/sandbox/sandbox-adapter.ts:17` only imports `SandboxManager` from the external package. `(allow file-read*)` with an empty deny list, deny-by-default writes with a hardcoded deny list for dotfiles and `.git`, unrestricted `process-exec` relying on sandbox inheritance, and a prompting HTTP/SOCKS proxy in the parent process for network.

**`codex-cli`** — the only in-tree syscall-level implementation in the set. Three composed `.sbpl` files on macOS (`references/codex-cli/codex-rs/sandboxing/src/seatbelt.rs:21-24`), bubblewrap with `--new-session --die-with-parent --unshare-user --unshare-pid` plus seccomp on Linux (`linux-sandbox/src/bwrap.rs:318-332`, `landlock.rs:169-268`). Four sandbox modes, four approval modes, per-command policy.

**`openclaw`** — the boundary is a Docker container, not a syscall filter. `references/openclaw/src/agents/sandbox/backend.ts:43-94` is a registry keyed on a global symbol with `docker` and `ssh` built in and `openshell` registered by a plugin; an unregistered backend is a hard refusal. Container flags are emitted in `src/agents/sandbox/docker.ts:411-535`. Defaults in `src/agents/sandbox/config.ts`: `readOnlyRoot: true` (`:108`), `network: "none"` (`:110`), `capDrop: ["ALL"]` (`:112`), plus an unconditional `--security-opt no-new-privileges` (`docker.ts:488`). `sandbox.mode` itself defaults to `"off"` (`config.ts:246`).

**`pi-mono`** — core is unguarded. `references/pi-mono/packages/coding-agent/src/core/tools/bash.ts:79-85` is a plain `spawn(shell, [...args, command])` with the parent environment, no approval prompt, and no workspace confinement on the file tools. There is no `--yolo` flag anywhere in the repo, because there is nothing to bypass. Isolation is a userland concern served by a real hook: `beforeToolCall` can block or mutate arguments (`src/core/agent-session.ts:397-416` → `packages/agent/src/agent-loop.ts:581-604`), and the shipped example at `examples/extensions/sandbox/index.ts` replaces the `bash` tool wholesale.

**`hermes-agent`** — no syscall sandbox on the default path. `references/hermes-agent/tools/terminal_tool.py:1013` reads `TERMINAL_ENV` with default `"local"`, and `tools/environments/local.py:493` runs `bash -c <model string>` as the same OS user. The substitute is a three-layer command guard described in §6, plus optional container backends where the container is explicitly declared the boundary and the whole guard layer is skipped (`tools/approval.py:1052-1054`).

**`opencode`** — `references/opencode/SECURITY.md:15-19` states the position directly: the permission system is a UX feature, not security isolation, and users wanting isolation should run opencode inside a container or VM. Sandbox escape is out of scope. The substitute is a three-effect rule table (`allow` / `ask` / `deny`) matched on tool name plus resource pattern, last match wins.

**`weclaw`** — the only harness in the set that removes isolation. It is a WeChat bridge that spawns `claude` or `codex`; `references/weclaw/agent/acp_agent.go:502-506` sends `"sandbox": "danger-full-access"` with `"approvalPolicy": "never"`, `:567-574` repeats it as `sandboxPolicy: {"type": "dangerFullAccess"}`, and `:718-722` auto-answers every `session/request_permission` with the allow option. Its only real boundary is a symlink-resolving containment check on outbound attachments (`messaging/attachment.go:51-75`), anchored to a root that a chat message can widen with `/cwd /` (`messaging/handler.go:646-663`).

**`pi-ai`** — 14 files, no tool layer, no `spawn`, no `child_process`. Nothing to isolate. Listed for completeness, not as a data point.

### 5. The four directions

| | `claude-code` | `codex-cli` | `openclaw` | `pi-mono` core | `pi-mono` + ext | `hermes-agent` | `opencode` | `weclaw` | **OpenProgram** |
|---|---|---|---|---|---|---|---|---|---|
| **Read** | whole disk, deny list ships empty | whole disk, deny-read engine ships empty | container sees two mounts only; **host** read tool unconfined | unconfined | `denyRead` ships loaded: `~/.ssh`, `~/.aws`, `~/.gnupg` | unconfined; file tool has a read-deny list the code calls "NOT a security boundary" | `*.env` → ask | unconfined | sandboxed local commands see the host minus the credential deny list; direct reads require `fs.read` authority but are not OS-sandboxed |
| **Write** | deny by default, allowlist plus hardcoded dotfile and `.git` denies | deny by default, `.git` / `.codex` / `.agents` protected | container: workspace mount is `:ro` unless `workspaceAccess: "rw"` | unconfined | `allowWrite: [".", "/tmp"]`, `denyWrite: .env`, `*.pem`, `*.key` | sensitive-path refusals for `/etc`, `/boot`, docker socket | rules only | unconfined | sandboxed commands and direct file tools enforce cwd/configured writable roots plus protected paths |
| **Execute** | unrestricted, children inherit the sandbox | unrestricted | unrestricted inside the container; **argv un-wrapper** blocks obfuscated invocations before the approval allowlist | unrestricted | unrestricted | 47-pattern regex plus an unbypassable hardline list | rules only | unrestricted | unrestricted, children inherit the sandbox |
| **Network** | prompting proxy, empty allowlist means every domain asks | off by default, optional proxy plus domain allowlist | `--network none` by default; `host` and `container:<id>` blocked | none | domain allow/deny list, 10 registry domains by default | `--network=none` exists but is **never passed**; unreachable on the shipped path | unrestricted | unrestricted | off on both platforms |
| **Child environment** | handled by the runtime | configurable, unfiltered by default | **name regex plus value heuristics**, ships loaded | full inheritance | full inheritance (the extension passes no `env`) | **stripped, derived from the provider registry** | unfiltered | full inheritance | allowlist, so an unknown name is dropped without a list update |

Two rows deserve emphasis because they invert what the first pass concluded from two harnesses.

**Credential blocking ships loaded in three of the eight.** `openclaw` refuses `.aws`, `.cargo`, `.config`, `.docker`, `.gnupg`, `.netrc`, `.npm` and `.ssh` as bind-mount sources plus `/etc`, `/proc`, `/sys`, `/dev`, `/root`, `/boot` and every docker-socket alias (`src/agents/sandbox/validate-sandbox-security.ts:23-49`), and blocks credential-shaped environment variables with a catch-all `/_?(API_KEY|TOKEN|PASSWORD|PRIVATE_KEY|SECRET)$/i` (`src/agents/sandbox/sanitize-env-vars.ts:1-19`). `hermes-agent` derives its child-environment blocklist from the provider registry rather than hardcoding it (`tools/environments/local.py:78-99`), so it cannot drift as providers are added. `pi-mono`'s sandbox extension ships `denyRead` and `denyWrite` populated and `enabled: true` (`examples/extensions/sandbox/index.ts:55-77`). The two that ship an empty engine are exactly the two studied first. Ours now ships loaded as well, with an allowlist rather than a derived blocklist for the environment: a provider added tomorrow is dropped without anyone updating anything, and `openclaw`'s catch-all name pattern is kept underneath as the floor on the one key that can add names back.

**Each of the three is partial, and each is partial in a way it documents.** `openclaw`'s denylist governs mount sources, not the host `read` tool, whose `tools.fs.workspaceOnly` defaults to `false`. `pi-mono`'s `denyRead` governs bash only, not its own `read` / `grep` / `find` tools. `hermes-agent` writes the caveat into the source: `agent/file_safety.py:167-171` states that the terminal tool runs as the same user and can `cat` the file anyway, so the read-deny is defence in depth and not a boundary. The lesson is not that partial coverage is acceptable; it is that the deny list belongs at the layer every path crosses, and that the uncovered paths have to be named.

### 6. Granularity, approval, and degradation

| | Granularity | Config surface | Sandbox × approval | When unavailable |
|---|---|---|---|---|
| `claude-code` | global switch × per-command opt-out | layered settings.json, no CLI flag | sandboxed bash **skips approval**; the prompt tells the model to retry with `dangerouslyDisableSandbox` on denial, which raises a prompt | runs anyway, warns loudly at startup; `failIfUnavailable` makes it a hard error |
| `codex-cli` | per-command, tool-overridable | config.toml plus `--sandbox` / `--add-dir` / `--yolo` | denied inside → ask → rerun outside, behind five gates | silent `None`; Windows downgrades to read-only; WSL1 hard error |
| `openclaw` | global × per-agent × per-tool × per-session (`mode: "non-main"`) | zod schema plus generated JSON schema, `agents.defaults.sandbox.*` and `agents.list[].sandbox.*` | **independent**: `host === "sandbox"` skips the approval allowlist entirely, the container is the boundary; `host === "gateway"` gets the allowlist | **hard refusal** with an actionable message, plus a `doctor` pre-flight warning |
| `pi-mono` | whole-tool allowlist (`--tools`), per-session | `~/.pi/agent/settings.json`; the extension has its own JSON, **project overrides global** | none in core; the example extension has no prompt | four separate paths set `sandboxEnabled = false` and continue, including "wrong platform" |
| `hermes-agent` | per-pattern approvals, per-context policy (cron, subagent, gateway, CLI, oneshot) | `cli-config.yaml`, `approvals.mode`, `command_allowlist`, `TERMINAL_ENV` | the guard **is** the approval system; container backends skip it wholesale | fail-open in non-interactive, oneshot and batch paths; fail-closed for subagents and the hardline list |
| `opencode` | per-tool × per-resource | opencode.json plus agent frontmatter | approval only, no sandbox | — |
| `weclaw` | none | `~/.weclaw/config.json`, no security key exists | approvals auto-answered "allow" | — |
| **OpenProgram** | one setting for the installation | seven `sandbox.*` keys in `config_schema.SETTINGS` | **unconnected** | **hard refusal** naming the two ways out, `warn` available |

The spread on the last column is the useful part. `openclaw` refuses to run when Docker is missing and prints the two ways out (`src/agents/sandbox/docker.ts:324-333`), and refuses to substitute a stock image for a missing one. `claude-code` runs anyway but its own source records why it must be noisy: a fixed bug where `isSandboxingEnabled()` silently returned false on missing dependencies is annotated "This is a security footgun — users configure allowedDomains expecting enforcement, get none." That is the behaviour we had, and `sandbox.unavailable_policy` now defaults to `refuse` instead.

### 7. Moves the first pass did not see

Eleven mechanisms appear in the five harnesses added in this pass and in neither `claude-code` nor `codex-cli`.

**Argv un-wrapping with a semantic-transparency test** (`openclaw`, `src/infra/dispatch-wrapper-resolution.ts:351-383`). A table of 18 launcher programs — `arch`, `caffeinate`, `chrt`, `doas`, `env`, `ionice`, `nice`, `nohup`, `sandbox-exec`, `script`, `setsid`, `stdbuf`, `sudo`, `taskset`, `time`, `timeout`, `xcrun` — is stripped from the front of an argv so the approval allowlist matches the real executable. Entries with no unwrap function (`sudo`, `doas`, `setsid`, `chrt`, `ionice`, `taskset`) are blocked outright. Entries that *can* be transparent are allowed only when the flags actually used do not change semantics: `nice -n 5` passes, `env FOO=bar` does not, `arch -e` does not, and `arch` / `xcrun` are transparent on darwin only. Chain depth is capped at 4 and overflow blocks (`:503-512`). The payoff is at `src/infra/exec-approvals-allowlist.ts:968-971`: a command whose wrapper chain is blocked **cannot have an "always allow" rule persisted for it**.

**Anti-obfuscation normalization before pattern matching** (`hermes-agent`, `tools/approval.py:452-467`). Full ECMA-48 ANSI escape stripping, null-byte removal, and Unicode NFKC normalization, so a fullwidth `ｒｍ　-ｒｆ　/` cannot slip past a regex. Paired with macOS `/private/*` symlink-mirror awareness at `:136-146`, so `/private/etc/sudoers` is matched as `/etc/sudoers`.

**A hardline floor below every bypass** (`hermes-agent`, `tools/approval.py:198-220`, checked at `:1060-1063`). Root deletes, `mkfs`, `dd` to a raw block device, fork bombs, `kill -1` and shutdown verbs are blocked **before** yolo mode, `approvals.mode=off` and cron approve-mode are consulted. The refusal text says so: "cannot be executed via the agent — not even with --yolo, /yolo, approvals.mode=off, or cron approve mode". The rationale at `:163-165` is that opting into yolo means trusting the agent with your files, not trusting it to wipe the disk.

**Treating password guessing as an attack, not a permission question** (`hermes-agent`, `tools/approval.py:238-266`). A `sudo -S` invocation with no `SUDO_PASSWORD` configured is blocked unconditionally, because the only reason to pipe into sudo's stdin is to try passwords.

**Self-preservation patterns** (`hermes-agent`, `tools/approval.py:360-375`). The agent is blocked from stopping or restarting its own gateway, from `hermes update`, and from `pkill hermes` including the structural `kill $(pgrep …)` and backtick forms.

**Per-context approval policy** (`hermes-agent`). Five contexts, five policies: cron defaults to deny (`cron_mode`), subagents default to auto-deny with an audit line (`tools/delegate_tool.py:73-84`, because an `input()` from a worker thread would deadlock the parent), the gateway uses an async queue, the CLI a synchronous prompt, and oneshot sets yolo because a prompt would hang forever (`hermes_cli/oneshot.py:170-172`).

**Blocking a nested agent's native execution surface** (`openclaw`, `extensions/codex/src/app-server/sandbox-guard.ts`). When openclaw drives a nested Codex app-server, it classifies every JSON-RPC method as `allowed-control-plane`, `blocked-native-bypass` or `requires-openclaw-environment`, blocks the prefixes `command/`, `fs/` and `windowsSandbox/` (`:62`), and injects `sandbox_exec` and `sandbox_process` replacements that route back through openclaw's own sandbox. This is the general answer to "the nested agent's built-in file tools are outside our reach".

**Policy-hash-driven sandbox recreation with epochs** (`openclaw`, `src/agents/sandbox/config-hash.ts:37-70`). A SHA-256 over the normalized effective policy is stamped on the container as a label and re-checked on reuse; a mismatch forces recreation. Named epoch constants (`SANDBOX_DOCKER_EXPLICIT_ENV_POLICY_EPOCH`, `SANDBOX_MOUNT_FORMAT_VERSION`) let a change in policy *semantics* invalidate live sandboxes without any config change.

**Linting your own security config** (`openclaw`, `src/security/audit-extra.sync.ts`). Named checks — `sandbox.docker_config_mode_off`, `sandbox.dangerous_bind_mount`, `sandbox.dangerous_network_mode`, `sandbox.dangerous_seccomp_profile`, `sandbox.dangerous_apparmor_profile`, and `tools.exec.host_sandbox_no_sandbox_defaults` for the case where `tools.exec.host="sandbox"` while `sandbox.mode="off"` — run against the user's configuration and report. The break-glass keys are a typed enumerated set (`DANGEROUS_SANDBOX_DOCKER_BOOLEAN_KEYS`, `src/agents/sandbox/config.ts:31-35`), each with a matching audit check.

**Resource limits and container lifecycle** (`openclaw` and `hermes-agent`). `openclaw` exposes `pidsLimit`, `memory`, `memorySwap`, `cpus`, `gpus` and `ulimits` (all unset by default) and prunes containers on idle-hours plus max-age (`src/agents/sandbox/prune.ts:24-36`, 24 h / 7 d). `hermes-agent` hardcodes `--pids-limit 256`, size-limited `nosuid` tmpfs for `/tmp`, `/var/tmp` and `/run`, `--cap-drop ALL` with three caps added back, `--security-opt no-new-privileges` and `--init` as PID 1 (`tools/environments/docker.py:161-171`), plus a 600 s foreground cap and a 50 KB output cap. Neither `claude-code` nor `codex-cli` has any resource limit beyond `RLIMIT_CORE=0`.

**Fail-closed on interceptor crash, and non-interactive means deny** (`pi-mono`). A `beforeToolCall` handler that throws a non-`Error` is converted into `Extension failed, blocking execution` (`src/core/agent-session.ts:410-415`). The shipped permission-gate example blocks when there is no UI (`examples/extensions/permission-gate.ts:20-23`), which is the correct default for CI and rarer than it should be. Note the asymmetry: the `user_bash` path swallows handler errors and proceeds.

Three anti-patterns from the same pass are worth recording because we have the equivalent surface.

**Project config overriding global sandbox config with no trust prompt** (`pi-mono`, `examples/extensions/sandbox/index.ts:79-102`). `deepMerge(deepMerge(DEFAULT_CONFIG, global), project)` means a cloned repository shipping `.pi/sandbox.json` with `{"enabled": false}` disables the user's machine-wide sandbox. Project-local extensions under `.pi/extensions/` also auto-load in-process with no trust gate.

**Truncation that hands the model the untruncated copy** (`pi-mono`, `src/core/tools/bash.ts:360-364`). Output is capped at 2000 lines / 50 KB, and the full text is written to `/tmp/pi-bash-*.log` with the path returned to the model, which can then `cat` it.

**A chat bridge dissolving the approval loop** (`weclaw`). `hermes-agent`'s ACP adapter forwards permission requests to the client (`acp_adapter/permissions.py:22-28`); `weclaw`'s answers them itself. Same protocol, opposite polarity. And `weclaw` has no sender allowlist: `messaging/handler.go:261-409` filters on message type and dedupes on message id, and never checks who sent it.

### 8. The credential question

The first pass, with two harnesses in view, concluded that nobody blocks credential reads out of the box and that both close the loop on the egress side instead — one routes all traffic through a per-domain prompting proxy, the other disables the network. With eight in view that conclusion narrows: **the two that ship an empty deny list are the minority**, and the three that ship loaded do so at different layers (mount sources, child environment, bash-only path globs) with the uncovered paths documented rather than hidden.

The reasoning that made an empty deny list defensible does not transfer here regardless. Network egress is already closed on both platforms, tighter than any of the eight. But the memory writer is an egress channel that never touches the network: it runs shell commands in a staging directory, its output is committed to the memory store, and the memory store is read back into the context of a later session. `cat ~/.openprogram/auth/*/default.json > topics/x.md` completes the exfiltration path entirely offline. Deny-read is a requirement here and an option there.

---

## Part III — Implementation decisions and record

### 9. Gap, precedent, step

Every gap measured in the pre-repair baseline, the reference implementation in Part II that informed the decision, and the implemented step in §10.

| Gap (Part I) | Who solved it, and how (Part II) | Step |
|---|---|---|
| `/dev/null` unwritable on macOS | `codex-cli` `seatbelt_base_policy.sbpl:18-21`; `claude-code` uses `require-all` with `vnode-type CHARACTER-DEVICE` | 1, **done** |
| exec allowlist breaks git, python, node | both open `process-exec` and rely on sandbox inheritance; `openclaw` restricts *obfuscation* instead of paths | 1, **done** |
| Linux tmpfs shadows a `/tmp` working directory | nobody has this bug; it is mount ordering | 1, **done** |
| whole-disk read, no deny-read engine | `openclaw` mount-source denylist, `hermes-agent` registry-derived env stripping, `pi-mono` extension `denyRead` — **three of eight ship loaded** | 2, **done** |
| full environment inheritance | `hermes-agent` `local.py:78-99` derives the blocklist from the provider registry; `openclaw` `sanitize-env-vars.ts:1-19` matches name patterns plus value heuristics | 2, **done** (allowlist instead of a derived blocklist) |
| Linux shared PID namespace, host processes readable and killable | `codex-cli` and `claude-code` both pass `--unshare-pid`; `claude-code` also limits `signal` and `process-info*` to `(target same-sandbox)` | 2, **done** |
| switch lost across thread, spawn and CLI boundaries | `codex-cli` rebuilds argv per exec; `openclaw` resolves policy per call site | 3, **done** |
| no config surface | every harness with a sandbox has one; `openclaw` generates a JSON schema from zod | 3, **done** |
| silent pass-through when unavailable | `openclaw` hard-refuses with an actionable message plus a `doctor` pre-flight; `claude-code`'s source calls the silent version a security footgun | 3, **done** |
| off by default and unusable when on | `codex-cli` ships `read-only` on by default | 4, **done** |
| sandbox and approval unconnected | `claude-code` skips approval inside the sandbox; `codex-cli` escalates out of it; `openclaw` treats the container as sufficient and skips the allowlist | 5, **done with an exact sandboxed retry** |
| `permission_mode="bypass"` short-circuits before the risky-tool check | `hermes-agent` puts its non-bypassable rules before every bypass | 5, **done** |
| prefix-matched command allowlist is bypassable by wrappers and Unicode | `openclaw` argv un-wrapper, `hermes-agent` NFKC plus ANSI normalization | 5, **done** |
| cron worker fires with no approval; sub-agents run with approval off | `hermes-agent` per-context policy: cron deny, subagent auto-deny with an audit line | 5, **done** |
| nested Claude Code CLI's file tools are unreachable | `openclaw` blocks the nested agent's `command/` and `fs/` methods and injects managed replacements | memory writer, **done** |
| no violation audit | `claude-code` attributes kernel deny lines to the command and feeds them back to the model; `openclaw` logs policy decisions on a dedicated `agents/tool-policy` logger | alongside 2, **done** |
| no CPU, memory, or process-count quotas | `hermes-agent` `--pids-limit 256`, sized tmpfs, 600 s foreground cap; `openclaw` exposes `pidsLimit` / `memory` / `cpus` / `ulimits` | **out of scope** |
| no config-file write protection | `claude-code` denies every settings.json explicitly to prevent escape; `codex-cli` protects `.codex` / `.git` / `.agents` | alongside 2, **done for protected program/config roots**; Git hooks and repository config remain opt-in for compatibility |
| no lint on our own sandbox settings | `openclaw` `src/security/audit-extra.sync.ts` named checks | separate configuration-safety work, not sandbox-runtime completion |

### 10. Repair order

These five steps have been implemented. The ordering below is retained to record the dependencies and acceptance criteria.

**1. Usability — done.** `process-exec` is unrestricted and children inherit the profile, so `git`, `python3`, `make`, `clang`, conda python and everything under `/sbin` and `/usr/sbin` run. `/dev/null`, `/dev/zero`, `/dev/random`, `/dev/urandom` and `/dev/tty` are readable and writable through `require-all` plus `vnode-type CHARACTER-DEVICE`, so `2>/dev/null` works. On Linux `--tmpfs /tmp` is emitted before the cwd bind, so a working directory under `/tmp` survives. What remains blocked on macOS is `ps` and `top`, because Seatbelt refuses to exec setuid binaries at all.

**2. Credential blocking — done.** The deny-read list ships loaded (§1.3). macOS emits `deny file-read*` and `deny file-write-unlink` per glob so a denied path cannot be probed by deletion; Linux masks directories with `--perms 0000 --tmpfs` and files with `--ro-bind /dev/null`, skipping paths that do not exist because the read-only root gives bubblewrap nowhere to create a mount point. The child environment is an allowlist rather than a blocklist derived from the provider registry: the derived list has to be rebuilt as providers are added, an allowlist drops an unknown name on its own, and `openclaw`'s catch-all name pattern is kept as the floor under `sandbox.pass_env`. Linux gains `--unshare-pid`, without which `/proc/<agent_pid>/environ` returns the key the environment filter just removed. Deny-write covers the agentics directory, and the protection is split by surface. On the file-tool surface (`write`, `edit`, `apply_patch`) it is unconditional: `validate_write_path()` refuses a write into the agentics directory or the agentic source registry before it resolves any policy, so no configuration reaches that check. On the command surface (`bash`, `execute_code`) the protection lives in the sandbox policy, so it holds under `workspace-write` and `read-only` and not under `sandbox.mode=danger-full-access`, where the shell face is unguarded by definition — that is what the mode means. Git hooks and git config are the same shape of escape but stay opt-in, because denying `.git/hooks/**` makes `git init` and `git clone` fail and there is no escalation path until step 5.

**3. Switch semantics — done.** The `ContextVar` is gone. The policy is resolved from `sandbox.*` in the config at the moment a command is wrapped, which survives the asyncio-task-to-thread hop, the `spawn` subprocess and a nested CLI, because a file does not belong to a context. It also sits below the permission layer, so `permission_mode="bypass"` short-circuits the approval card and not the sandbox. `wrap_command` takes an explicit policy for callers that hold one; nothing yet supplies a different policy per tool or per call site, which is what "the call site can override" was meant to buy. An unavailable backend refuses by default and names the two ways out.

**4. On by default — done.** New installations use `workspace-write`; an existing explicit `danger-full-access` is preserved.

**5. Approval integration — done, with three distinct decisions.**

*Forward:* read-only tools, explicit allow rules and safe edit paths already skip approval. Merely being sandboxed does not exempt arbitrary bash because `workspace-write` can still modify or delete repository files.

*Backward:* a structured sandbox denial can request one exact local-owner approval. The retry uses an escalated OS-sandbox policy rather than unrestricted host execution; the agentics hard floor and credential filtering remain active.

*Downward:* `_hard_constraint_violation` and capability checks run before rules, approval and bypass. Cron and subagents cannot open an interactive approval path. Command matching removes ANSI and NUL, applies NFKC, parses transparent `env` wrappers and refuses to persist complex shell expressions.

Permission rules and `SandboxPolicy` remain separate inputs because they answer different questions: owner consent versus the resources the process can actually reach.

The profile patch list is complete: the working directory is escaped before interpolation, signal and process inspection are limited to the same sandbox, Linux receives the namespace and capability flags above, both paths use `/bin/bash`, macOS sysctl access is narrowed, general `mach-lookup` is removed, and temporary writes are limited to the current `TMPDIR`. `--unshare-user` remains absent because the non-setuid bubblewrap build creates a user namespace itself and the setuid build rejects the flag.

CPU, memory, and process-count quotas are explicitly outside this sandbox project. Linux PID namespaces hide host processes but do not impose a process-count quota. Generic configuration linting is separate configuration-safety work; the ignored provider keyword `sandbox="read-only"` is documented as non-enforcing and is not part of the runtime boundary.

### Connecting the memory writer

The memory writer has two execution surfaces and only one of them is reachable by `wrap_command`.

The MCP `shell` tool (`memory/management/tools.py` → `workspace.shell()`) runs inside the OpenProgram process. It calls `LocalBackend._invocation(..., force_sandbox=True)` with the staging directory as cwd, so it refuses to execute when the host-native backend is unavailable even if interactive bash was explicitly turned off.

The Claude Code CLI subprocess (`memory/agent_runtime/claude_code.py`) is a different matter. Its `Read`, `Write`, `Edit`, `Grep` and `Glob` execute inside the CLI process, where `wrap_command` cannot reach them, and `permission_mode="dontAsk"` disables its own approvals. The CLI process must not be sandboxed either, because it calls the Anthropic API and the sandbox has no network.

The CLI process remains outside the OS sandbox so that it can call the Anthropic API. Its built-in `Read`, `Write`, `Edit`, `Grep`, `Glob`, and `Bash` tools are now explicitly disabled. OpenProgram injects MCP replacements for the five file operations; those replacements resolve paths against the staged workspace, reject writes under `sources/`, and append every result to the writer audit. The MCP `shell` replacement uses `LocalBackend._invocation(..., force_sandbox=True)` and refuses to execute when the platform sandbox is unavailable.

The current boundary is therefore explicit: **the nested CLI has API access but no built-in file or command tools; all exposed file and command operations are host-managed MCP tools, and commands are forced through the OS sandbox.**

The threat this closes is concrete rather than hypothetical. Any text that reaches the writer's prompt is attacker-influenced whenever a message channel is attached, since the inbound message body carries a display name the sender sets themselves. A command that gets executed from there could read `~/.openprogram/auth/*/default.json` and write it into a topic file, and the memory store returns to the context of a later session — an exfiltration path that never touches the network, which is why the network being off did not cover it.

### 11. Denial legibility and negotiation

The enforcement layer is complete; this section designs the layer above it. The motivating incident is measured, not hypothetical: an agent hit the `**/.env` deny-read rule under `permission_mode="bypass"`, received a bare `Operation not permitted`, and spent several turns relocating the key into `secrets/glee.env` — a workaround that succeeds because the deny list matches file names, not content. The boundary held, but the outcome was negotiated around the owner instead of with the owner. The same failure shape appears on the other side of the fence: Claude Code's community documents "five rounds" of configuration to make one `.env` deny hold across its two uncoordinated layers, and its own docs warn that a broad `allowRead` silently re-exposes what a deny meant to protect. The shared lesson: a boundary that cannot explain itself either gets worked around (ours) or misconfigured (theirs). Our gap is legibility, not enforcement.

Five parts, ordered by leverage:

**Named denials.** A sandbox denial that reaches the model as a tool result names the matched deny glob and states the two legitimate ways forward: request escalation (which raises the owner card), or ask the owner to change `sandbox.deny_read`. The text explicitly rules out the third way: relocating or copying the protected content so a different path escapes the glob. Today the model reverse-engineers `Operation not permitted` from platform error text; after this change the denial is a routable instruction. The `sandbox.violation` event already carries the structure — this extends the model-facing text, not the event.

**A negotiation card, with a persistent outcome.** The escalation approval renders as a dedicated card showing the blocked path, the matched rule, and the risk framing, with three choices. *Allow once* is the existing exact escalated retry. *Always allow this path* is new: it writes the concrete path into `sandbox.allow_read`, a new config key whose semantics follow the narrower-path-wins rule Claude Code documents for its `allowRead`/`denyRead` overlap — an allow entry re-opens only the named path inside a wider deny, and an equally-specific deny still beats it. The non-configurable floor (`~/.openprogram/auth/**`, the agentics directory) is excluded from `sandbox.allow_read` resolution entirely, so no card click can open it. *Deny* stands. This closes the loop the incident exposed: the legitimate path (one card click) becomes cheaper than the workaround (several turns of file relocation).

**Visible state.** The chat top bar's permission badge gains a sandbox indicator, and the `bypass` label changes to say what it means: "Bypass approvals (sandbox still on)". The mode never promised to remove the OS boundary, but nothing on screen said so; the incident's owner confusion ("I enabled bypass, why is there a sandbox?") is a caption bug as much as anything.

**Presets over keys.** A Security panel presents both layers on one screen — permission rules beside sandbox policy — with three named presets: *strict* (the shipped defaults), *balanced* (`**/.env` removed from deny-read, credentials and network still closed), *open* (`danger-full-access`, rendered as the warning it is). Editing a preset keeps the two layers coherent without the owner learning glob semantics — the direct answer to the cross-layer configuration burden Claude Code's users report.

**Incentive alignment in the prompt.** One system-prompt line: when the sandbox blocks a read, request escalation or surface the block to the user; never move or copy secrets to defeat a path rule. Enforcement cannot distinguish a helpful relocation from an exfiltration staging step, so the honest path has to be the instructed one — and, after the negotiation card, also the shortest one.

What this deliberately does not change: the two-layer architecture (approval decides, sandbox confines), the deny-before-bypass ordering, the hard floor, and the loaded defaults. The design adds no second enforcement point; every part above is presentation, persistence, or prompt.

---

## Implementation status

As of 2026-08-10, repair steps 1–5 and the expanded architecture steps 04–08 are implemented. New installations default to `workspace-write`; an explicit existing `danger-full-access` remains unchanged.

- Hard constraints and the fixed authority-tier capability check run before permission rules, approval and `permission_mode="bypass"`.
- Cron stores a signed immutable execution spec with its owner tier and runs unattended with a forced sandbox and no approval escalation. `execute_code`, spawned agentic processes and one-shot MCP calls use the same policy boundary.
- `write`, `edit` and `apply_patch` enforce writable roots. Auto-import only accepts owner-recorded program sources, including a checked migration for existing official clones.
- Paired channel speech is trusted and may append source memory. Unpaired group speech never enters the agent and is archived as `pending`; pending evidence remains retrievable, while hold-queue admission and read filtering are deferred. Only a local interactive owner can promote it.
- Sandbox denials are structured. Only a local interactive owner can approve one exact retry under an escalated policy that retains the hard floor and credential filtering. Persistent approval stores the normalized exact operation; complex shell expressions are once-only.
- Nested Claude Code built-ins are disabled and replaced by managed MCP file and shell tools.

Section 11 (denial legibility and negotiation) is designed but not yet implemented as of 2026-08-22: denials do not yet name the matched rule, `sandbox.allow_read` does not exist, the escalation card has no "always allow this path" choice, the permission badge shows no sandbox state, and there is no Security presets panel.

Final verification on 2026-08-10: the complete tracked local suite excluding integration reports 2731 passed, 4 skipped and 1 expected failure; GitHub Actions run 31398444213 passes Python 3.11, 3.12 and 3.13, Web, docs and examples, with the Linux 3.11 job reporting 2723 passed, 12 skipped and 1 expected failure. That runner enables Ubuntu 24.04's unprivileged-user-namespace facility before the real cron bubblewrap test, so an installed-but-inoperable binary is not counted as Linux coverage. The real macOS Seatbelt and Linux bubblewrap matrices cover git, Python, npm, make, conda, credential denial, outside-workspace denial and network denial.

Known limits:

- Windows has no host-native sandbox backend. With sandboxing enabled, commands are refused by default; OpenProgram does not select Docker automatically. An owner must explicitly set `sandbox.mode=danger-full-access` or select the unsafe `sandbox.unavailable_policy=warn` behavior.
- `ps` and `top` do not run inside Seatbelt because macOS refuses to execute setuid binaries in this profile.
- Linux cannot express a middle-wildcard deny-read pattern such as `**/.env`; known concrete paths are masked, while this glob remains macOS-only. Protect sensitive Linux content with an exact path or a concrete directory rule such as `/absolute/path/to/secrets/**`.
- Sandbox policy is installation-wide. There is no per-tool sandbox override; authority and permission rules remain separate per-operation controls.
- Git hooks and repository config remain writable inside the workspace so normal `git init` and `git clone` work; owners can add them to `sandbox.deny_write`.
- CPU, memory, and process-count quotas are out of scope. The current timeout and Linux namespace controls are not described as resource quotas.
