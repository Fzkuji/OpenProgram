# Sandbox

The sandbox is the **process isolation layer**: it wraps a shell command so the child process it spawns can only write inside the working directory and cannot reach the network. It sits underneath the permission system, which is a decision layer and does no isolation of its own ([`permission-model.md`](permission-model.md) §1.1). The two are independent today: a command can be approved and unsandboxed, or sandboxed and unapproved.

A rendered walkthrough of the same material lives at [`sandbox-architecture.html`](sandbox-architecture.html).

The whole implementation is `openprogram/sandbox/__init__.py`, 65 lines, three public names: `sandbox_enabled` (a `ContextVar[bool]`, default `False`), `is_available()`, and `wrap_command(command, cwd) -> (args, shell)`. Its only consumer is `openprogram/backend/local.py::_invocation`, which wraps the command when `sandbox_enabled` is true **and** the platform tool is present.

---

## 1. The boundary

Four directions, and they are not symmetric. Reads are unrestricted, writes are confined, execution is restricted on macOS only, and the network is off on both platforms.

### 1.1 macOS — Seatbelt

`wrap_command` returns `/usr/bin/sandbox-exec -p <profile> /bin/bash -c <command>`. The profile is generated inline by `_seatbelt_profile()`:

| Resource | Policy |
|---|---|
| Fallback | `(deny default)` |
| File read | `(allow file-read* (subpath "/"))` — **the whole disk** |
| File write | cwd, `/private/var/folders`, `/private/tmp`, `/tmp` |
| Process exec | `/bin`, `/usr/bin`, `/usr/local/bin`, `/opt/homebrew` |
| fork | allowed |
| sysctl | `(allow sysctl-read)`, no name filter |
| Mach IPC | `(allow mach-lookup)`, no name filter |
| Network | no rule, so `(deny default)` blocks both directions |
| `/dev` write | no rule, so `(deny default)` blocks it, `/dev/null` included |
| Signals, POSIX IPC, shared memory | no rule, blocked |

### 1.2 Linux — bubblewrap

```
bwrap --ro-bind / / --bind <cwd> <cwd> --tmpfs /tmp --proc /proc --dev /dev
      --unshare-net -- bash -c <command>
```

| Resource | Policy |
|---|---|
| File read | `--ro-bind / /` — **the whole disk** |
| File write | cwd only, plus a throwaway tmpfs at `/tmp` |
| Process exec | **unrestricted**, any binary anywhere |
| Network | `--unshare-net`, loopback only |
| PID namespace | **not unshared** |
| Other namespaces | ipc / uts / user / cgroup all shared |
| Terminal | no `--new-session` (bubblewrap documents it as the TIOCSTI injection guard) |
| Lifetime | no `--die-with-parent`, children outlive the parent |
| Syscalls | no seccomp filter |
| Environment | no `--clearenv`, full inheritance |

### 1.3 What sits on the readable side

Because reads are unrestricted on both platforms, a sandboxed command reads every credential on the machine: SSH private keys, the OAuth and API-key payloads under `~/.openprogram/auth/`, `~/.claude.json`, `~/.config/gh/hosts.yml`, and the raw keychain database on macOS. The environment is inherited whole, so `OPENAI_API_KEY` and its siblings are visible to every child process. On Linux the PID namespace is shared, so `/proc/<pid>/environ` of any process outside the sandbox is readable and any same-uid process can be signalled, including `kill -9`.

Network egress is closed on both platforms, so a credential read inside the sandbox cannot be sent anywhere directly. It can be written to `/tmp` or `/private/var/folders` and picked up by the next unsandboxed command, and — once the memory writer runs shell commands — it can be written into the memory store, which returns to the context in a later session. §5 develops why that second path changes the conclusion.

### 1.4 macOS blocks ordinary work

The exec allowlist and the missing `/dev` write rule take out the commands a coding agent actually runs:

| Command | Result | Cause |
|---|---|---|
| `echo hi > /dev/null` | `Operation not permitted` | `/dev` is not in the write allowlist |
| `ls /nonexistent 2>/dev/null` | fails | same; `2>/dev/null` appears in most real commands |
| `git` | `can't exec '/Library/Developer/CommandLineTools/usr/bin/git'` | `/usr/bin/git` is a shim, the real binary lives outside the allowlist |
| `python3`, `make`, `clang` | same | same shim path |
| conda python, nvm node | `Operation not permitted` | not in the allowlist |
| `ps`, `top` | `Operation not permitted` | setuid binaries, Seatbelt denies exec |
| `/sbin/*`, `/usr/sbin/*` | denied | not in the allowlist |
| `/opt/homebrew/bin/*` | works | the one allowlist entry that carries real tools |

The allowlist does not restrict execution in any meaningful sense — `/bin/bash -c` is itself allowlisted and can read a script from anywhere. It restricts interpreter *paths*, which hits the toolchain and not an attacker.

### 1.5 Linux shadows the working directory

`bwrap` applies mounts in argument order. `--bind <cwd> <cwd>` comes before `--tmpfs /tmp`, so when the working directory is under `/tmp` the tmpfs covers it and the workspace disappears inside the sandbox: `ls`, `cat` and writes all fail with "No such file or directory" while the host copy is untouched. Any `tempfile`-created staging directory hits this, which includes the memory writer's stage directory.

### 1.6 The working directory is interpolated into the profile unescaped

`_seatbelt_profile()` builds `(allow file-write* (subpath "{cwd}"))` with an f-string. A path that closes the string and opens another rule stays balanced and parses, widening the write scope to an arbitrary directory. Unbalanced payloads make `sandbox-exec` fail to parse and the command does not run, so the failure mode is closed. No model-controlled path reaches this today: worktree paths go through `_slugify()` in `openprogram/worktree/manager.py` and are reduced to `[A-Za-z0-9_-]`, and every other working directory comes from the user's project or `OPENPROGRAM_WORKDIR`. It is latent rather than reachable, and the fix is one line.

The profile is also passed as `argv[2]`, so any process on the machine can read the current write scope out of the process table.

---

## 2. The switch

`sandbox_enabled` is a process-level boolean carried on a `ContextVar`. Two toggles set it, both manual: `/sandbox` in the CLI REPL (`openprogram/_cli_chat/handlers.py::_handle_sandbox`) and `/sandbox` in the web UI (`openprogram/webui/ws_actions/chat.py::handle_sandbox`). There is no config key — `SETTINGS` in `openprogram/config_schema.py` has no `sandbox` entry — no environment variable, and no profile field.

**The web toggle does not reach the command.** `handle_sandbox` runs inside the websocket's asyncio task and calls `sandbox_enabled.set(True)` there. The agent turn runs in a bare `threading.Thread` started by the same module, and a new thread begins with an empty `Context`, so the read inside `_invocation` returns the default `False`. There is no `copy_context()` anywhere under `openprogram/webui/`. The UI shows "Sandbox: ON" and the commands run unwrapped. This is a missed edit rather than a design choice: `openprogram/functions/_runtime.py` and `openprogram/agent/task/runner.py` both copy the context correctly when they hand work to another thread.

**The CLI toggle works on the same thread only.** A bash command issued in the REPL after the toggle is wrapped.

**Subprocesses always lose it.** `openprogram/agent/process_runner.py` uses `mp.get_context("spawn")`, and spawn does not carry context variables; the module restores the usage context explicitly and nothing else. Every bash call inside an `@agentic_function` therefore runs unwrapped.

**Degrading is silent.** `_invocation` reads `if sandbox_enabled.get(False) and _sandbox_available():` and falls through to plain execution otherwise. Both toggles refuse to turn on when the platform tool is missing, but a state that is already ON degrades without a word if the tool disappears.

There is no granularity: not per agent, not per tool, not per command. One process-level boolean.

---

## 3. Coverage

About 25 places in the repository turn content into a running process. One of them goes through the sandbox.

| Execution point | What runs | Command source | Sandboxed |
|---|---|---|---|
| `functions/tools/bash/bash.py` → `backend/local.py::_invocation` | arbitrary shell string | model | **yes**, when the switch is on and the local backend is active |
| `backend/docker.py`, `backend/ssh.py` | `docker run` / `ssh <target> "…"` | model | no |
| `functions/tools/process/process.py` | long-lived `backend.spawn()` | model | follows bash, off by default |
| `functions/tools/execute_code/execute_code.py` | `subprocess.run([python, script])`, model-written script, model-chosen interpreter | model | no, and the module header states isolation is out of scope |
| `functions/tools/cron/worker.py` | `Popen(command, shell=True, start_new_session=True)` and `python -m openprogram.cli deep-work` | model | no, survives the session, no approval at fire time |
| `memory/scriptorium/management/workspace.py` | `subprocess.run(command, shell=True, cwd=stage_dir)` | model, via the MCP `shell` tool | no |
| `memory/scriptorium/agent_runtime/claude_code.py` | nested Claude Code SDK agent, `permission_mode="dontAsk"` | model-driven | no |
| `functions/_registry.py` `exec_module`, driven by `functions/watcher.py` | imports `.py` files under `functions/agentics/`, module-level code runs in the agent process | model can write that directory | **no, and no user action is involved** |
| `webui/_functions.py` | re-execs the module on every UI run | model can write it | no |
| `agent/process_runner.py` | spawned subprocess running an `@agentic_function` | model | no |
| `plugins/loader.py` | in-process `importlib.import_module()` | plugin manifest | no; `plugins/sandbox.py::load_subprocess` raises `NotImplementedError` |
| `mcp/client.py` | stdio MCP servers with `env={**os.environ, …}` | config file, which bash can rewrite | no |
| `webui/routes/mcp.py` | spawns a command straight from an HTTP body | HTTP caller | no, despite the docstring calling it a one-shot sandbox |
| `events/shell_hooks.py` | `subprocess.run(command, shell=True, input=<event JSON>)` | user config, model content on stdin | no, fail-open on timeout |
| `providers/_shared/cli_backend/runner.py` | claude / codex / gemini CLIs | config plus prompt | no; nothing imports this module today |
| grep / worktree / agent_browser / git plumbing | argv lists with `--` separators | model arguments | no sandbox, not injectable |
| `_cli_cmds/*`, `plugins/installer.py` | `git clone`, `pip install`, `npm i -g` | the user's own CLI invocation | no, as expected |

Three of these deserve separate attention.

**The shortest path from model output to host code does not involve bash.** `functions/watcher.py` polls `functions/agentics/` every two seconds and calls `rescan()`, which reaches `spec.loader.exec_module(module)`. A `.py` file written there by the `write` tool executes at module level inside the agent process within seconds, with no tool approval and nothing for `wrap_command` to wrap.

**A spawned sub-agent runs with approval turned off.** `openprogram/agent/sub_agent_run.py` sets `permission_mode="bypass"` on the turn it creates, and `_gated_execute` short-circuits on bypass at step ③, before the `_RISKY_TOOLS` check that would otherwise catch bash, execute_code and process. Rule-layer deny and ask still apply, since they are evaluated first.

**One argument looks protective and is not.** `webui/_runtime_management.py` passes `full_auto=False, sandbox="read-only"` into `create_runtime()`, and `providers/openai_codex/runtime.py` documents those kwargs as accepted and ignored.

---

## 4. How this compares to other harnesses

| | Claude Code | Codex CLI | opencode | OpenProgram |
|---|---|---|---|---|
| OS-level isolation | yes, external runtime package | yes, in-tree | **declines to do it** | yes, thin |
| On by default | no | yes (`read-only`) | — | no |
| Granularity | global switch × per-command opt-out | per-command, tool-overridable | per-tool × per-resource | one process-level boolean |
| Config surface | layered settings.json | config.toml + CLI flags | opencode.json + agent frontmatter | **none** |
| Sandbox ↔ approval | inside the sandbox, bash **skips approval** | denied inside → ask → rerun outside | no sandbox, approval only | **unconnected** |
| Network | prompting proxy, empty allowlist means every domain asks | off by default, optional proxy plus domain allowlist | unrestricted | off on both platforms |
| Credential read blocking | engine present, **ships empty** | engine present, **ships empty** | `*.env` → ask | **no engine** |
| Config-file write protection | yes, explicitly to prevent escape | yes (`.codex`, `.git`, `.agents`) | no | **no** |
| Unavailable backend | runs anyway, warns loudly at startup | silent none, Windows downgrade, WSL1 hard error | — | **silent pass-through** |
| Audit | kernel deny log, attributed per command, fed back to the model | structured violation events plus OTel | permission event bus | **none** |
| Child environment | handled by the runtime | configurable, unfiltered by default | unfiltered | **unfiltered** |
| exec restriction | none, children inherit the sandbox | none | — | macOS allowlist that breaks git and python |
| Process isolation | `signal` and `process-info*` limited to the same sandbox; `--unshare-pid` on Linux | `--unshare-pid` plus seccomp against ptrace | — | macOS covered by `deny default`; **Linux can kill host processes** |
| Scope | Bash and PowerShell only | child processes only | — | the bash tool only |

Three points are common to both harnesses that do implement a sandbox, and they are design constraints here too:

1. **Only child processes are sandboxed. The agent process never is**, because it has to reach the model API.
2. **A sandbox that is not wired into approval does not get used.** One harness trades "no approval prompt inside the sandbox" for adoption; the other trades "fail inside, ask, rerun outside" for usability.
3. **Execution is not restricted by binary path.** Both let children exec anything and rely on sandbox inheritance instead.

### The credential inversion

Neither reference harness blocks credential reads out of the box. One is `(allow file-read*)` with an empty deny list; the other has a working deny-read engine with no default entries. Both close the loop on the **egress** side instead: one routes all traffic through a proxy that prompts per domain, the other disables the network. Reading a key is harmless when it cannot leave.

That reasoning does not carry over here. Network egress is already closed on both platforms, tighter than either reference. But the memory writer is an egress channel that never touches the network: it runs shell commands in a staging directory, its output is committed to the memory store, and the memory store is read back into the context of a later session. `cat ~/.openprogram/auth/*/default.json > topics/x.md` completes the exfiltration path entirely offline. That is why deny-read is a requirement here and optional there.

---

## 5. Repair order

Five steps, in dependency order. Each one is a precondition for the value of the next.

**1. Usability.** Add `/dev/null`, `/dev/zero` and `/dev/urandom` read and write to the macOS profile. Replace the exec allowlist with unrestricted `process-exec` and rely on sandbox inheritance, matching both reference harnesses. Fix the Linux mount ordering so a working directory under `/tmp` gets its tmpfs mounted before the bind rather than after. *Skipped:* turning the sandbox on makes `git`, `python3`, `node` and `2>/dev/null` fail on macOS and silently deletes a `/tmp` workspace on Linux, so the first thing any user does is turn it off again — which is the state the code is in now.

**2. Credential blocking.** Add a deny-read glob list to both platforms, loaded with entries out of the box: `~/.ssh/**`, `~/.aws/**`, `~/.openprogram/auth/**`, `~/.claude.json`, `**/.env`, `~/Library/Keychains/**`. Emit `deny file-read*` and `deny file-write-unlink` together on macOS, so a denied path cannot be probed by deletion. Pass a child environment allowlist (`PATH SHELL HOME LANG TMPDIR USER` plus explicit config) instead of inheriting everything, and add `--unshare-pid` on Linux, without which `/proc/<agent_pid>/environ` leaks the key that scrubbing the child environment just removed. *Skipped:* the sandbox does not defend against the one thing worth defending against here, and connecting the memory writer completes a network-free exfiltration path.

**3. Switch semantics.** Remove the `ContextVar` and make the policy an explicit parameter: `Backend.run(command, timeout, cwd, *, sandbox: SandboxPolicy | None)`. Tool definitions carry the default, session settings supply it, the call site can override. Add `sandbox.*` keys to `config_schema.py::SETTINGS` — mode, writable roots, deny-read globs, network, behaviour when unavailable — so the generated config reference picks them up. Make the unavailable case refuse by default with an explanatory error instead of passing through silently. *Skipped:* the switch is lost at three boundaries (asyncio task to thread, spawn, nested CLI), two of them measured, so on the web path and inside every agentic function the sandbox is off no matter what the UI shows.

**4. On by default,** with `workspace-write` semantics. *Skipped:* a mechanism that is off by default and has no config key never runs in practice, which makes the previous three steps unobservable.

**5. Approval integration,** in both directions. Forward: a bash command that will run sandboxed with an unwidened policy skips the approval card, so enabling the sandbox costs the user fewer confirmations rather than more. Backward: a command denied inside the sandbox raises an approval request carrying the reason, and reruns unsandboxed on approval — the cheap substitute for a domain allowlist, since `pip install`, `npm i` and `git fetch` all resolve through it. Permission rules and sandbox policy should also share a source: a user's `deny: Read(~/.ssh/**)` becomes a sandbox deny-read entry. *Skipped:* the sandbox stays a pure tax — same approval prompts, fewer working commands — and nobody turns it on.

Alongside these, the profiles need their own patch list: narrow `sysctl-read` and `mach-lookup` to named allowlists (the current blanket grants make the clipboard readable and leave the Apple Events channel open), narrow `/private/var/folders` to the process `TMPDIR`, escape the working directory before interpolating it into the profile, add `(allow signal (target same-sandbox))` and `(allow process-info* (target same-sandbox))` before exec is opened up, add `--new-session --die-with-parent --unshare-user --cap-drop ALL` on Linux, protect `.git` and `functions/agentics/` from writes, and use `/bin/bash` on both paths so toggling the sandbox does not also change the shell.

### Connecting the memory writer

The memory writer has two execution surfaces and only one of them is reachable by `wrap_command`.

The MCP `shell` tool (`memory/scriptorium/management/tools.py` → `workspace.shell()`) runs `subprocess.run(command, shell=True, cwd=stage_dir)` inside the OpenProgram process. Its shape is identical to `_invocation`, so wrapping it is a few lines.

The Claude Code CLI subprocess (`memory/scriptorium/agent_runtime/claude_code.py`) is a different matter. Its `Read`, `Write`, `Edit`, `Grep` and `Glob` execute inside the CLI process, where `wrap_command` cannot reach them, and `permission_mode="dontAsk"` disables its own approvals. The CLI process must not be sandboxed either, because it calls the Anthropic API and the sandbox has no network. The accurate boundary after connecting is therefore: **the MCP `shell` tool is sandboxed, the SDK's built-in file tools are not**, and constraining the latter is a job for `allowed_tools` and the CLI's own isolation, not for this layer.

Steps 1 and 2 are prerequisites — without step 1 the sandbox erases the staging directory on Linux and breaks `git` and `2>/dev/null` on macOS, and without step 2 sandboxing the shell does not address the exfiltration path that motivates sandboxing it. Step 3 matters because the writer runs on background threads and in subprocesses where the `ContextVar` does not arrive.

---

## Implementation status

Landed: §1 (both platform profiles, exactly as described), §2 (the `ContextVar`, both toggles, the silent degradation), §3 (the coverage table reflects the code as it stands).

Not implemented:

- Deny-read globs, child-environment filtering, config-file and `.git` write protection — no engine exists on either platform.
- `sandbox.*` config keys; the switch is toggle-only.
- Per-command policy; the switch is one process-level boolean.
- Any link between the sandbox and the permission system, in either direction.
- Violation auditing, resource limits, and Windows support.
- The macOS profile patches in §5 and the Linux `bwrap` flags in §5.

Known defects, measured:

- The web UI toggle sets the flag in an asyncio task while the agent turn runs in a bare thread, so it has no effect on the executed command.
- `spawn` subprocesses do not carry the flag, so bash inside every `@agentic_function` is unwrapped.
- On Linux, a working directory under `/tmp` is shadowed by `--tmpfs /tmp` and its contents vanish inside the sandbox.
- On Linux, the shared PID namespace lets a sandboxed process read `/proc/<pid>/environ` of processes outside the sandbox and signal them.
- On macOS, `/dev/null` is unwritable and the exec allowlist blocks `git`, `python3`, `node`, `ps` and everything under `/sbin` and `/usr/sbin`.
- The working directory is interpolated into the SBPL profile without escaping; a crafted path widens the write scope. Not reachable from model-controlled input today.
