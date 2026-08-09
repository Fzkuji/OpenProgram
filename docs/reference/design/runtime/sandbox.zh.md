# 沙箱（Sandbox）

沙箱是**进程隔离层**：把一条shell命令包起来，让它起的子进程只能写工作目录、碰不到网络。它在权限系统下面一层，权限系统是决策层、自己不做任何隔离（见[`permission-model.md`](permission-model.md) §1.1）。两者目前互不知情：一条命令可以批过但不进沙箱，也可以进了沙箱但没批过。

同一份内容的图示版在[`sandbox-architecture.html`](sandbox-architecture.html)。

实现全部在`openprogram/sandbox/__init__.py`，65行，对外三个名字：`sandbox_enabled`（`ContextVar[bool]`，默认`False`）、`is_available()`、`wrap_command(command, cwd) -> (args, shell)`。唯一的消费者是`openprogram/backend/local.py::_invocation`，它在开关为真**且**平台工具存在时才包装命令。

---

## 1. 边界

四个方向，不对称：读不限、写受限、执行只在macOS受限、网络两个平台都断。

### 1.1 macOS：Seatbelt

`wrap_command`返回`/usr/bin/sandbox-exec -p <profile> /bin/bash -c <command>`，profile由`_seatbelt_profile()`内联生成：

| 资源 | 策略 |
|---|---|
| 兜底 | `(deny default)` |
| 文件读 | `(allow file-read* (subpath "/"))`，**整盘** |
| 文件写 | cwd、`/private/var/folders`、`/private/tmp`、`/tmp` |
| 进程执行 | `/bin`、`/usr/bin`、`/usr/local/bin`、`/opt/homebrew` |
| fork | 允许 |
| sysctl | `(allow sysctl-read)`，无名字过滤 |
| Mach IPC | `(allow mach-lookup)`，无名字过滤 |
| 网络 | 没写规则，被`(deny default)`兜住，出入站都断 |
| `/dev`写 | 没写规则，被`(deny default)`兜住，含`/dev/null` |
| 信号、POSIX IPC、共享内存 | 没写规则，拦下 |

### 1.2 Linux：bubblewrap

```
bwrap --ro-bind / / --bind <cwd> <cwd> --tmpfs /tmp --proc /proc --dev /dev
      --unshare-net -- bash -c <command>
```

| 资源 | 策略 |
|---|---|
| 文件读 | `--ro-bind / /`，**整盘** |
| 文件写 | 只有cwd，外加一个用完即弃的`/tmp` tmpfs |
| 进程执行 | **完全不限制**，任何位置的任何二进制 |
| 网络 | `--unshare-net`，只剩回环 |
| PID命名空间 | **没有隔离** |
| 其余命名空间 | ipc/uts/user/cgroup全共享 |
| 终端 | 没有`--new-session`（bubblewrap自己把它列为TIOCSTI注入防护） |
| 生命周期 | 没有`--die-with-parent`，父进程死了子进程留着 |
| 系统调用 | 无seccomp过滤 |
| 环境变量 | 没有`--clearenv`，全量继承 |

### 1.3 可读的那一侧有什么

两个平台的读都不受限，所以沙箱内的命令能读到本机全部凭证：SSH私钥、`~/.openprogram/auth/`下的OAuth载荷和API key、`~/.claude.json`、`~/.config/gh/hosts.yml`，macOS上还有keychain数据库原始字节。环境变量全量继承，`OPENAI_API_KEY`这类值对每个子进程可见。Linux上PID命名空间是共享的，沙箱外任意进程的`/proc/<pid>/environ`可读，同uid进程可被信号打断，包括`kill -9`。

出站网络两个平台都断，所以读到的凭证不能直接送出去。但它可以写进`/tmp`或`/private/var/folders`，等下一条不带沙箱的命令来取；memory写入器一旦跑起shell命令，它还可以写进记忆库，而记忆库内容会在之后的会话里回到上下文。§5说明第二条路径为什么改变了结论。

### 1.4 macOS把正常工作也挡住了

exec白名单加上缺失的`/dev`写规则，正好干掉编码agent真正要跑的命令：

| 命令 | 结果 | 原因 |
|---|---|---|
| `echo hi > /dev/null` | `Operation not permitted` | `/dev`不在写白名单 |
| `ls /nonexistent 2>/dev/null` | 报错 | 同上，`2>/dev/null`在真实命令里遍地都是 |
| `git` | `can't exec '/Library/Developer/CommandLineTools/usr/bin/git'` | `/usr/bin/git`是跳板，真身在白名单外 |
| `python3`、`make`、`clang` | 同上 | 同一条跳板路径 |
| conda python、nvm node | `Operation not permitted` | 不在白名单 |
| `ps`、`top` | `Operation not permitted` | setuid二进制，Seatbelt默认不给exec |
| `/sbin/*`、`/usr/sbin/*` | 拒绝 | 不在白名单 |
| `/opt/homebrew/bin/*` | 正常 | 白名单里唯一真正带工具的一条 |

这份白名单并没有在任何有意义的层面限制执行：`/bin/bash -c`本身就在白名单里，脚本可以从任何地方读进来。它限制的是解释器路径，挡住的是工具链而不是攻击者。

### 1.5 Linux会把工作目录盖掉

`bwrap`按参数顺序应用挂载。`--bind <cwd> <cwd>`在前、`--tmpfs /tmp`在后，所以工作目录落在`/tmp`下时tmpfs会把它盖住，沙箱内工作区直接消失：`ls`、`cat`和写全部报"No such file or directory"，宿主侧文件却还在。任何用`tempfile`创建的暂存目录都会撞上这条，包括memory写入器的stage目录。

### 1.6 工作目录未转义拼进profile

`_seatbelt_profile()`用f-string拼出`(allow file-write* (subpath "{cwd}"))`。一条先闭合字符串再开一条新规则的路径，括号是配平的、能正常解析，写范围就被放宽到任意目录。括号不配平的payload会让`sandbox-exec`解析失败、命令不执行，所以失败方向是关闭的。目前没有模型可控的路径能走到这里：worktree路径经`openprogram/worktree/manager.py`的`_slugify()`过滤成`[A-Za-z0-9_-]`，其余工作目录来自用户项目或`OPENPROGRAM_WORKDIR`。这是潜伏问题而非当前可达漏洞，修起来是一行。

profile还作为`argv[2]`传给`sandbox-exec`，同机任何进程都能从进程表里读到当前放宽了哪些写路径。

---

## 2. 开关

`sandbox_enabled`是挂在`ContextVar`上的进程级布尔值。两个入口，都是手动toggle：CLI REPL的`/sandbox`（`openprogram/_cli_chat/handlers.py::_handle_sandbox`）和Web UI的`/sandbox`（`openprogram/webui/ws_actions/chat.py::handle_sandbox`）。没有配置项，`openprogram/config_schema.py`的`SETTINGS`里没有`sandbox`条目，没有环境变量，没有profile字段。

**Web的toggle到不了命令。**`handle_sandbox`在websocket的asyncio任务里`sandbox_enabled.set(True)`，而agent轮次跑在同一个模块起的裸`threading.Thread`里，新线程拿到的是空`Context`，`_invocation`读回默认值`False`。`openprogram/webui/`下没有任何`copy_context()`。界面显示"Sandbox: ON"，命令照常裸跑。这是漏改不是设计：`openprogram/functions/_runtime.py`和`openprogram/agent/task/runner.py`把活交给别的线程时都正确拷了上下文。

**CLI的toggle只在同线程有效。**REPL里toggle之后发出的bash命令确实被包住。

**子进程一律丢失。**`openprogram/agent/process_runner.py`用`mp.get_context("spawn")`，spawn不携带上下文变量；该模块只显式恢复了usage上下文。所以每个`@agentic_function`里的bash都是裸跑。

**降级是静默的。**`_invocation`的判断是`if sandbox_enabled.get(False) and _sandbox_available():`，否则直接落到普通执行分支。两个toggle在平台工具缺失时会拒绝打开，但已经是ON的状态下工具消失，就是无声降级。

没有粒度：不分agent、不分工具、不分命令，一个进程级布尔。

---

## 3. 覆盖面

全仓库约25处会把内容变成运行中的进程，走沙箱的只有1处。

| 执行点 | 跑的是什么 | 命令来源 | 沙箱 |
|---|---|---|---|
| `functions/tools/bash/bash.py` → `backend/local.py::_invocation` | 任意shell串 | 模型 | **是**，需开关为真且用local backend |
| `backend/docker.py`、`backend/ssh.py` | `docker run` / `ssh <target> "…"` | 模型 | 无 |
| `functions/tools/process/process.py` | 长驻`backend.spawn()` | 模型 | 跟随bash，默认关 |
| `functions/tools/execute_code/execute_code.py` | `subprocess.run([python, script])`，脚本由模型写、解释器路径也是模型参数 | 模型 | 无，文件头明写隔离不是本工具的职责 |
| `functions/tools/cron/worker.py` | `Popen(command, shell=True, start_new_session=True)`和`python -m openprogram.cli deep-work` | 模型 | 无，脱离会话存活，触发时没有审批 |
| `memory/scriptorium/management/workspace.py` | `subprocess.run(command, shell=True, cwd=stage_dir)` | 模型，经MCP`shell`工具 | 无 |
| `memory/scriptorium/agent_runtime/claude_code.py` | 嵌套Claude Code SDK agent，`permission_mode="dontAsk"` | 模型驱动 | 无 |
| `functions/_registry.py`的`exec_module`，由`functions/watcher.py`驱动 | 导入`functions/agentics/`下的`.py`，模块级代码在agent进程内执行 | 模型可写该目录 | **无，且不需要任何用户动作** |
| `webui/_functions.py` | 每次UI运行重新exec该模块 | 模型可写 | 无 |
| `agent/process_runner.py` | spawn子进程跑一个`@agentic_function` | 模型 | 无 |
| `plugins/loader.py` | 进程内`importlib.import_module()` | 插件清单 | 无，`plugins/sandbox.py::load_subprocess`抛`NotImplementedError` |
| `mcp/client.py` | stdio MCP server，`env={**os.environ, …}` | 配置文件，bash可改写 | 无 |
| `webui/routes/mcp.py` | 直接从HTTP请求体拿命令起进程 | HTTP调用方 | 无，尽管docstring自称one-shot sandbox |
| `events/shell_hooks.py` | `subprocess.run(command, shell=True, input=<事件JSON>)` | 命令来自用户配置，stdin是模型内容 | 无，超时fail-open |
| `providers/_shared/cli_backend/runner.py` | claude/codex/gemini CLI | 配置加prompt | 无，且当前没有代码import这个模块 |
| grep、worktree、agent_browser、git管道 | 带`--`分隔的argv列表 | 模型参数 | 无沙箱，不可注入 |
| `_cli_cmds/*`、`plugins/installer.py` | `git clone`、`pip install`、`npm i -g` | 用户自己敲的CLI | 无，符合预期 |

其中三条要单独说。

**从模型输出到宿主代码的最短路径不经过bash。**`functions/watcher.py`每两秒轮询`functions/agentics/`并调`rescan()`，最终走到`spec.loader.exec_module(module)`。模型用`write`往那儿放一个`.py`，几秒内模块级代码就在agent进程里跑起来，不经工具审批，也没有任何东西可以让`wrap_command`去包。

**spawn出来的子agent关掉了审批。**`openprogram/agent/sub_agent_run.py`给新建的轮次设`permission_mode="bypass"`，`_gated_execute`在第③步就因bypass短路，位置在会拦住bash/execute_code/process的`_RISKY_TOOLS`检查之前。规则层的deny和ask仍然生效，它们排在更前面。

**一个看起来在防护的参数并不防护。**`webui/_runtime_management.py`往`create_runtime()`传`full_auto=False, sandbox="read-only"`，而`providers/openai_codex/runtime.py`把这些kwarg明确记为接受即忽略。

---

## 4. 和其他harness的对比

| | Claude Code | Codex CLI | opencode | OpenProgram |
|---|---|---|---|---|
| 操作系统级隔离 | 有，外部runtime包 | 有，自研 | **明确不做** | 有，很薄 |
| 默认开 | 否 | 是（`read-only`） | — | 否 |
| 粒度 | 全局开关×单命令opt-out | per-command，工具可覆盖 | per-tool×per-resource | 一个进程级布尔 |
| 配置入口 | 分层settings.json | config.toml加CLI flag | opencode.json加agent frontmatter | **无** |
| 沙箱与审批 | 沙箱内bash**免审批** | 沙箱内被拒→问→不带沙箱重跑 | 无沙箱，纯审批 | **不联动** |
| 网络 | 提示式代理，空白名单意味着每个域名都问 | 默认断，可配代理和域名白名单 | 不限制 | 两个平台都断 |
| 凭证读屏蔽 | 引擎有，**出厂为空** | 引擎有，**出厂为空** | `*.env`→ask | **无引擎** |
| 配置文件写保护 | 有，明确为了防逃逸 | 有（`.codex`/`.git`/`.agents`） | 无 | **无** |
| 后端不可用 | 照跑但启动时明确告警 | 静默None、Windows降级、WSL1硬报错 | — | **静默放行** |
| 审计 | 内核deny日志按命令归因并喂回模型 | 结构化违规事件加OTel | 权限事件总线 | **无** |
| 子进程环境变量 | 由runtime处理 | 可配置，默认不过滤 | 不过滤 | **不过滤** |
| exec限制 | 无，子进程继承沙箱 | 无 | — | macOS有白名单，误伤git和python |
| 进程隔离 | `signal`和`process-info*`限同沙箱，Linux有`--unshare-pid` | `--unshare-pid`加seccomp封ptrace | — | macOS靠`deny default`，**Linux可杀宿主进程** |
| 受管范围 | 只有Bash和PowerShell | 只有子进程 | — | 只有bash工具 |

两个真的做了沙箱的实现有三条共识，这里同样成立：

1. **只包子进程，agent进程本身永远不进沙箱**，因为它要调模型API。
2. **不和审批联动的沙箱没人开。**一家用"沙箱内不弹窗"换采用率，另一家用"里面失败就问、批了再不带沙箱重跑"换可用性。
3. **不靠二进制路径限制执行。**两家都放开exec，靠子进程继承沙箱兜底。

### 凭证这条为什么反过来

两个参考实现出厂都不屏蔽凭证读取。一家是`(allow file-read*)`加空denylist，另一家有可用的deny-read引擎但没有默认条目。它们都在**外传侧**收口：一家把流量全走代理、逐个域名弹窗，另一家默认断网。读到了送不出去，读就无所谓。

这条推理在这里不成立。出站网络本来就断，比两家都严；但memory写入器是一条不碰网络的外传通道：它在暂存目录里跑shell命令，产出提交进记忆库，记忆库内容又会在之后的会话里回到上下文。`cat ~/.openprogram/auth/*/default.json > topics/x.md`全程离线就完成了外传。所以deny-read在这里是必需项，在那边是可选项。

---

## 5. 修复顺序

五步，按依赖排。每一步都是下一步能产生价值的前提。

**1. 可用性。**macOS profile补上`/dev/null`、`/dev/zero`、`/dev/urandom`的读写；exec白名单换成不限制`process-exec`、靠子进程继承沙箱兜底，和两个参考实现一致；Linux按路径判断挂载顺序，工作目录落在`/tmp`下时先挂tmpfs再bind。**不做的后果**：一开沙箱，macOS上`git`、`python3`、`node`和`2>/dev/null`全部失败，Linux上`/tmp`里的工作区静默消失，用户第一件事就是把它关掉，也就是现在这个状态。

**2. 凭证屏蔽。**两个平台加deny-read glob列表，并且出厂就装弹：`~/.ssh/**`、`~/.aws/**`、`~/.openprogram/auth/**`、`~/.claude.json`、`**/.env`、`~/Library/Keychains/**`。macOS上`deny file-read*`要和`deny file-write-unlink`成对发，否则被禁读的路径可以用删除操作反推存在性。子进程环境变量按白名单传（`PATH SHELL HOME LANG TMPDIR USER`加显式配置项），不再全量继承；Linux同时加`--unshare-pid`，否则刚洗干净的key又能从`/proc/<agent_pid>/environ`读回来。**不做的后果**：沙箱防不住这里唯一值得防的东西，而接上memory写入器就补齐了一条不需要网络的凭证外传链路。

**3. 开关语义。**砍掉`ContextVar`，改成显式参数：`Backend.run(command, timeout, cwd, *, sandbox: SandboxPolicy | None)`。工具定义带默认值，会话设置提供默认来源，调用点可覆盖。`config_schema.py`的`SETTINGS`加一组`sandbox.*`键（模式、可写根、deny-read glob、网络、不可用时的行为），生成的配置参考页自动收录。不可用时默认拒绝执行并说明原因，不再静默放行。**不做的后果**：开关在三个边界上丢失（asyncio任务到线程、spawn、嵌套CLI），其中两处已实测，Web路径和每个agentic function里沙箱都是关的，界面显示什么都不影响。

**4. 默认开**，`workspace-write`语义。**不做的后果**：一个默认关又没有配置项的机制在真实使用中不会运行，前三步全都无从检验。

**5. 和审批联动**，两个方向。正向：一条将在沙箱内执行、策略未被放宽的bash命令跳过审批卡，开沙箱对用户变成少点几次确认。反向：沙箱内被拒的命令带上原因发起审批，批准后不带沙箱重跑，这是域名白名单的廉价替代品，`pip install`、`npm i`、`git fetch`都走这条路解决。权限规则和沙箱策略还应共用一个来源：用户写的`deny: Read(~/.ssh/**)`同时成为沙箱的deny-read条目。**不做的后果**：沙箱纯粹是额外负担，审批一次不少、能跑的命令变少，没人会打开它。

配套还有profile自身的修补清单：`sysctl-read`和`mach-lookup`从全放行收成具名白名单（现在的全开让剪贴板可读、Apple Events通道开着），`/private/var/folders`收窄到本进程的`TMPDIR`，工作目录拼进profile前先转义，放开exec之前补上`(allow signal (target same-sandbox))`和`(allow process-info* (target same-sandbox))`，Linux加`--new-session --die-with-parent --unshare-user --cap-drop ALL`，把`.git`和`functions/agentics/`加进禁写，两条路径统一用`/bin/bash`，切换沙箱不应该顺带换掉shell。

### 接memory写入器

memory写入器有两个执行面，`wrap_command`只够得着其中一个。

MCP`shell`工具（`memory/scriptorium/management/tools.py` → `workspace.shell()`）在OpenProgram进程内跑`subprocess.run(command, shell=True, cwd=stage_dir)`，形状和`_invocation`完全一致，包起来是几行的事。

Claude Code CLI子进程（`memory/scriptorium/agent_runtime/claude_code.py`）是另一回事。它的`Read`/`Write`/`Edit`/`Grep`/`Glob`在CLI进程内部执行，`wrap_command`碰不到，而`permission_mode="dontAsk"`把它自己的审批也关了。这个CLI进程也不该被包进沙箱：它要调Anthropic API，而沙箱没有网络。所以接上之后的准确边界是：**MCP`shell`受限，SDK自带的文件工具不受限**，约束后者要靠`allowed_tools`和Claude Code自身的隔离能力，不是这一层的事。

第1、2步是硬前置：没有第1步，Linux上沙箱会抹掉暂存目录、macOS上`git`和`2>/dev/null`会坏；没有第2步，把shell包起来并没有解决要它解决的那个问题。第3步同样必要，因为写入器跑在后台线程和子进程里，`ContextVar`到不了。

---

## 实现状态

已落地：§1（两个平台的profile，和文中描述一致）、§2（`ContextVar`、两个toggle、静默降级）、§3（覆盖面表就是当前代码的状态）。

未实现：

- deny-read glob、子进程环境变量过滤、配置文件与`.git`写保护，两个平台都没有引擎。
- `sandbox.*`配置键，现在只有toggle。
- per-command策略，现在是一个进程级布尔。
- 沙箱和权限系统的任何方向的联动。
- 违规审计、资源限额、Windows支持。
- §5列的macOS profile修补和Linux `bwrap`参数。

已实测的缺陷：

- Web UI的toggle在asyncio任务里设标志，agent轮次跑在裸线程里，对实际执行的命令没有影响。
- `spawn`子进程不携带该标志，所以每个`@agentic_function`里的bash都是裸跑。
- Linux上工作目录落在`/tmp`下时被`--tmpfs /tmp`盖掉，沙箱内看不到内容。
- Linux上PID命名空间共享，沙箱内可读沙箱外进程的`/proc/<pid>/environ`并对其发信号。
- macOS上`/dev/null`不可写，exec白名单挡住`git`、`python3`、`node`、`ps`以及`/sbin`和`/usr/sbin`下的一切。
- 工作目录未转义就拼进SBPL profile，构造的路径可放宽写范围。目前从模型可控输入到不了这里。
