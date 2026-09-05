# Windows support

Windows support is delivered in independently useful levels. The native
CLI/server, browser UI, release installer, and signed Desktop distribution are
implemented as separate layers. Windows sandboxing is an optional WSL2
delegation layer and does not make the native CLI depend on WSL.

## Support levels

| Level | Contract |
|---|---|
| W0 | Community fixes are accepted, known gaps are documented, and unsupported entry points fail with an explicit message. No Windows support is promised. |
| W1 | The native CLI and server run on Windows, the full Ink terminal UI and browser UI are available, MCP token management is functional, and the Windows CI contracts pass. Terminals without raw input fall back to Rich. Sandbox execution and Desktop are outside this level. |
| W2 | Windows release archives, a PowerShell release installer, and Windows-specific `doctor` checks are supported. Doctor covers long-path configuration and gives an advisory Defender exclusion hint without changing Defender settings. |
| W3 | The Electron Desktop application is distributed with signing and automatic updates. Unsigned builds are not a supported release channel because SmartScreen makes them unsuitable for ordinary users. |
| W4 | Local command isolation delegates to bubblewrap inside an installed WSL2 distribution. `auto` capability detection keeps native Windows usable when that optional backend is absent. AppContainer and Job Objects are not presented as equivalent filesystem and network isolation. |

W2 remains the supported fallback for machines that do not install Desktop.
W3 is the current Desktop distribution target. W4 is optional and independently
deployable.

## Engineering rules

Platform-specific behavior lives in `openprogram/_compat.py` or a platform
adapter selected by that seam. Product modules use capability detection where
possible. Unsupported functionality fails at its entry point instead of
raising a late `NotImplementedError`. A weaker replacement for a POSIX safety
property is an explicit, documented decision. A platform fix is incomplete
until its Windows CI contract covers it.

POSIX `0600` and `0700` modes are not Windows requirements. Windows W1 uses the
ACL inherited from the user's profile and does not remove ACL inheritance or
rewrite access entries. Atomic file replacement and process-safe file locking
remain functional requirements because they prevent partial or lost writes;
they are not permission-hardening policy.

## W1 implementation

The source-development installer creates an isolated checkout `.venv`, installs
the npm lockfile, builds the browser and Ink terminal interfaces, installs the
selected Python extras, and exposes a stable `openprogram.cmd` launcher on the
user `PATH`. `-Minimal` installs only the Python CLI/server path.

Ink startup is capability-based on every operating system. Windows Terminal
and ConPTY preserve the inherited stdin console handle and run the full-screen
TUI. MinTTY and other terminals that cannot enter raw input fail at the UI
boundary and restore stdio before the Rich fallback starts. ConPTY may deliver
printable text and Enter in one read, so the input tokenizer splits control
bytes from printable spans; bracketed paste remains one atomic input event.

MCP token creation uses a unique temporary file and atomic hard-link
publication so concurrent creators cannot overwrite one another. Reads still
revalidate the opened regular file and its directory ancestry. Windows keeps
the profile's inherited ACL and intentionally does not emulate POSIX ownership
or `0600` mode bits.

Windows process inspection uses PowerShell CIM rather than WMIC. Persistent
workers use a least-privilege per-user Task Scheduler task. Checkpoint history,
Undo/Reapply, review diffs, backup creation, and transactional restore select a
path-based fallback when CPython does not expose descriptor-relative directory
operations. The fallback rejects symlinks and junction/reparse traversal,
revalidates parent identity, and uses binary and atomic file operations.

Process liveness and creation-time identity use read-only native Windows
queries. Signal zero is never used on Windows; it is not a portable existence
probe. File-operation journals use the shared cross-process lock adapter.
Forward apply and rollback keep distinct guard files so Windows rename rules
cannot turn a recoverable failure into a stranded transaction.

Project-file queries use directory capabilities from the compatibility seam.
Workspace review reads Git tree/index metadata and immutable objects in bounded
batches, rather than starting Git for each changed file. The regression contract
limits Git invocation count independently of the number of changed files.

Self-update journal reads follow the same inherited-ACL Windows contract as the
rest of the runtime. This makes state projection and recovery records portable;
it does not establish support for the separate macOS-specific controller and
installer pipeline, which still needs a Windows implementation and native
end-to-end acceptance before it can be advertised as available.

Local shell tools use Git Bash when it is installed and otherwise fall back to
the Windows PowerShell included with the operating system; they never depend on
`cmd.exe` parsing Bash-oriented commands. Background shell and process-tree
cleanup helpers use no-window process creation so Desktop agent runs do not
flash console windows. The tool contract describes the active surface as a host
shell and directs portable file work to the dedicated file/search tools or
Python rather than assuming Unix coreutils are installed.

Program discovery uses direct distribution metadata lookups for the catalogued
applications. It does not rebuild Python's complete import-to-distribution map
for every Program or every WebSocket connection; that full filesystem walk is
especially expensive in the complete Windows runtime and would delay session
and page data after every hard refresh.

The Windows CI surface has two parts:

- the core job covers compatibility seams, checkpoint history, backup/restore,
  upgrade behavior, the installer contract, and the Task Scheduler adapter;
- the installation smoke job performs a complete PowerShell installation,
  checks the isolated environment and Web build, starts the worker, runs
  `doctor`, and stops the worker.

## W2 implementation

The formal Release matrix builds complete Windows x86_64 and arm64 product
runtimes on native Windows runners and publishes deterministic
`OpenProgram-<version>-runtime-windows-<arch>.zip` archives plus SHA-256 files. The
ZIP has one `runtime/` root and contains managed CPython, a platform Node.js
executable, a self-contained Ink bundle, prebuilt Web and docs assets,
providers, channels, Programs, Playwright Chromium, and model data. The runtime
verifier executes an Ink startup probe and records `tui.ink` only after it
succeeds.

The public `install.ps1` bootstrap resolves a stable release and downloads the
PowerShell installer from that immutable tag. The installer validates the
checksum and every ZIP entry before extraction, rejects links and reparse
points, verifies the runtime capability manifest, and completes a worker cold
start in isolated state. Only then does it atomically replace the active
PowerShell launcher. Releases stay in versioned directories, and the previous
launcher is retained; a failed installation never changes the active launcher.
No installer step edits ACLs.

Managed upgrades select the Windows ZIP and tagged PowerShell installer
through the compatibility seam. `doctor` reports long-path registry state and
Defender real-time scanning/exclusion state as non-blocking advice. These
queries are read-only and do not enable long paths or change Defender.

## W3 implementation

The tag workflow builds Windows x86_64 and arm64 Electron applications and
assisted, per-user NSIS installers. It stages the exact complete W2 runtime through the
formal PowerShell release installer before packaging, then smoke-tests the
embedded runtime, worker health endpoint, Web chat route, and immutable Program
boundary from `win-unpacked`. Both `OpenProgram.exe` and the installer must have
a valid Authenticode signature. Missing signing credentials or an invalid
signature fails the release job; unsigned Windows builds are local development
artifacts, not publishable releases.

Packaged Desktop starts the worker with its embedded managed Python. The native
Terminal selects Windows PowerShell, falling back to `COMSPEC`, and asks
`node-pty` for ConPTY. Browser-profile import discovers Chrome, Edge, Brave, and
Chromium under their normal Windows installation and local-app-data locations.
Windows Desktop state inherits the user's existing ACL; no packaging, import,
or update path rewrites it.

The Desktop updater selects the exact `OpenProgram-<version>-win-<arch>.exe`
release asset for the running x64 or arm64 architecture. It validates release metadata, byte count, and SHA-256, then
requires valid Authenticode before opening the installer. A failed validation
deletes the candidate. Installation remains a visible user-confirmed handoff;
the running application is not silently replaced.

Windows CI installs Desktop dependencies including the native PTY module and
runs the same Desktop contract suite. The suite covers ConPTY command
selection, browser import, cross-window tab transactions, packaged worker
bootstrap, release selection, checksum failure, and signature failure.

## W4 implementation

The Windows command sandbox delegates to the default WSL2 distribution and
runs the command through bubblewrap. The compatibility seam discovers a real
WSL2 distribution, verifies Bash and bubblewrap, probes that a namespace can be
created, and translates Windows paths with that distribution's `wslpath`.
Bubblewrap then supplies the same main boundaries used on Linux: a read-only
root, explicitly writable workspace roots, read and write deny paths, isolated
PID/IPC/UTS namespaces, a private temporary directory, and a network namespace
unless the effective policy permits network access.

The default `sandbox.mode` is `auto`. On macOS and Linux it enables the native
backend when available. On Windows it enables WSL2 delegation only after the
capability probe succeeds; otherwise commands remain native and unsandboxed so
an optional backend cannot make the CLI unusable. Choosing
`workspace-write` explicitly remains fail-closed and reports the missing WSL2
or bubblewrap prerequisite at the execution entry point. No probe or sandbox
setup changes Windows ACLs, ownership, file modes, Defender, or WSL settings.

AppContainer is not selected because making a useful existing workspace
visible would require application-specific capability and ACL work. Job
Objects remain useful for future CPU, memory, and process-count limits, but do
not provide the filesystem and network boundary required by the current
sandbox contract. Native AppContainer isolation and resource quotas therefore
remain separate future work rather than incomplete parts of W4.

## Implementation status

| Level | Status |
|---|---|
| W0 | Implemented as the baseline compatibility contract. |
| W1 | Implementation is present and locally validated; the repository Windows jobs are the merge gate. |
| W2 | Implemented; the Windows Release build and installer jobs are the publication gate. |
| W3 | Implemented and locally validated; configured signing credentials and the Windows Desktop release job are the publication gate. |
| W4 | Implemented through optional WSL2 and bubblewrap delegation; native AppContainer isolation and resource quotas remain future work. |
