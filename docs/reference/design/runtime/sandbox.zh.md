# 沙箱（Sandbox）

沙箱是**进程隔离层**：把一条shell命令包起来，让它起的子进程只能写工作目录、碰不到网络。它在权限系统下面一层，权限系统是决策层、自己不做任何隔离（见[`permission-model.md`](permission-model.md) §1.1）。两者目前互不知情：一条命令可以批过但不进沙箱，也可以进了沙箱但没批过。

同一份内容的图示版在[`sandbox-architecture.html`](sandbox-architecture.html)。

**本文分三层。**[第一部分](#第一部分我们现在怎么做)是当前实现，全部实测。[第二部分](#第二部分别人怎么做)是`references/`下八个框架各自的做法，包括明确不做沙箱的那几个。[第三部分](#第三部分我们计划怎么做)是计划，每一步都标出它补的是第一部分的哪条缺口、抄的是第二部分的哪一家。

---

## 第一部分：我们现在怎么做

实现全部在`openprogram/sandbox/__init__.py`。对外的名字是`SandboxPolicy`（冻结dataclass）、`resolve_policy()`、`is_available()` / `unavailable_reason()`、`child_env(policy)`、`wrap_command(command, cwd, policy) -> (args, shell)`。有两个消费者：`openprogram/backend/local.py::_invocation`，bash和process工具走它；`memory/scriptorium/management/workspace.py::shell`，memory写入器的MCP `shell`工具走它。

### 1. 边界

四个方向，不对称：读除凭证glob之外不限，写限制在工作目录，执行不限制，网络两个平台都断。这一节全部是在已发布代码上实测的。

#### 1.1 macOS：Seatbelt

`wrap_command`返回`/usr/bin/sandbox-exec -p <profile> /bin/bash -c <command>`，profile由`_seatbelt_profile()`内联生成：

| 资源 | 策略 |
|---|---|
| 兜底 | `(deny default)` |
| 文件读 | `(allow file-read* (subpath "/"))`，之后每条deny-read glob各发一条`deny file-read*`正则 |
| 文件写 | cwd、额外的`writable_roots`、`/private/var/folders`、`/private/tmp`、`/tmp`，之后每条deny-write glob各发一条`deny file-write*`正则 |
| 删除被禁路径 | 每条deny-read glob同时发`deny file-write-unlink`，被禁读的路径不能用删除操作反推存在性 |
| 进程执行 | `(allow process-exec)`，不限制，子进程继承profile |
| fork | 允许 |
| 信号、进程信息 | 只限`(target same-sandbox)` |
| POSIX信号量与共享内存 | 允许，给Python multiprocessing用 |
| 字符设备 | `/dev/null`、`/dev/zero`、`/dev/random`、`/dev/urandom`、`/dev/tty`的读写和ioctl，每条都用`require-all`加`vnode-type CHARACTER-DEVICE` |
| sysctl | `(allow sysctl-read)`，无名字过滤 |
| Mach IPC | `(allow mach-lookup)`，无名字过滤 |
| 网络 | 除非`sandbox.network`打开，否则不发任何规则，由`(deny default)`兜住出入两个方向 |

工作目录拼进profile之前先转义，deny glob编译成锚定正则。Seatbelt正则方言有两条细节是关键的：`(?:…)`永远匹配不上，非捕获分组会让一条deny规则变成无声空转；引擎匹配的是解析软链后的真实路径，所以每条glob在静态前缀指向别处时要发两遍。

#### 1.2 Linux：bubblewrap

```
bwrap --new-session --die-with-parent --unshare-pid --unshare-ipc --unshare-uts
      --cap-drop ALL --ro-bind / / --proc /proc --dev /dev [--unshare-net]
      --tmpfs /tmp --bind <cwd> <cwd> [屏蔽挂载] -- /bin/bash -c <command>
```

| 资源 | 策略 |
|---|---|
| 文件读 | `--ro-bind / /`，减去下面的deny-read挂载 |
| 文件写 | cwd和额外`writable_roots`用`--bind`放开，加上`/tmp`的一次性tmpfs |
| deny-read目录 | `--perms 0000 --tmpfs <dir>`；`--cap-drop ALL`拿掉了`DAC_OVERRIDE`，所以容器里子进程是root时权限位依然生效 |
| deny-read文件 | `--ro-bind /dev/null <file>` |
| 进程执行 | 不限制，任何位置的任何二进制 |
| 网络 | 除非`sandbox.network`打开，否则`--unshare-net` |
| PID命名空间 | 已隔离 |
| IPC、UTS命名空间 | 已隔离 |
| capability | 全部丢弃 |
| 终端 | `--new-session`，bubblewrap文档把它列为TIOCSTI注入防护 |
| 生命周期 | `--die-with-parent` |
| 系统调用 | 无seccomp过滤 |
| 环境变量 | 由调用方过滤，见§1.4 |

挂载顺序是功能性的，不是格式问题。`--tmpfs /tmp`必须发在cwd bind**之前**；反过来的话，工作目录落在`/tmp`下时会被tmpfs盖掉，沙箱内工作区消失而宿主侧文件完好。所有`tempfile`建的暂存目录都落在那里，包括memory写入器的暂存目录。屏蔽挂载会跳过宿主上不存在的路径：根是只读绑定的，bubblewrap没法在里面创建挂载点，硬来会让整条调用失败并报`Can't create file at <path>: Read-only file system`。

`--unshare-user`是故意不加的。非setuid的构建本来就自己建用户命名空间，加了没有增量；setuid的构建根本不支持这个参数。

#### 1.3 屏蔽清单里有什么

清单出厂就装弹，不是空的：`~/.ssh/**`、`~/.aws/**`、`~/.gnupg/**`、`~/.openprogram/auth/**`、`~/.claude.json`、`~/.claude/.credentials.json`、`~/.config/gh/**`、`~/.netrc`、`~/Library/Keychains/**`、`**/.env`。实测开着沙箱时，这里每一条读都在macOS上报`Operation not permitted`、在Linux上报`Permission denied`，`rm -f ~/.ssh/id_ed25519`同样被拒，不会泄露文件在不在。

清单之外仍然全盘可读。这是有意选的姿态而不是疏漏：全盘读是命令了解自己所在系统的方式，收口收在携带凭证的路径上，不是收在读这个动作上。

#### 1.4 子进程的环境变量

沙箱内的子进程拿到的是一份白名单：`PATH`、`HOME`、`SHELL`、`USER`、`LOGNAME`、`TERM`、`TMPDIR`、`TMP`、`TEMP`、`TZ`、`PWD`、`OLDPWD`、`LANG`、`LANGUAGE`、`COLUMNS`、`LINES`和`LC_*`，再加上`sandbox.pass_env`里名字本身不像凭证的条目。实测：父进程里164字符的`OPENAI_API_KEY`到子进程是空串，`env | grep -iE '(key|token|secret|password)='`没有输出。

选白名单而不是从provider注册表推导的黑名单，理由只有一条：明天新增的provider会被自动丢掉，没人需要更新任何东西，而推导出来的名单要跟着注册表一起长。凭证名字pattern留作`sandbox.pass_env`的底线，避免这个逃生口顺手把key发给每一条命令。

Linux上光洗环境变量不够。没有`--unshare-pid`时，`/proc/<agent_pid>/environ`会把刚从子进程里去掉的key还回来。加上之后，沙箱内的进程只看得到4个PID，`cat /proc/<宿主pid>/environ`报"No such file or directory"，`kill -9 <宿主pid>`报"No such process"，宿主进程照常活着。

#### 1.5 还敞着的部分

- macOS上`ps`和`top`跑不了。它们是setuid二进制，Seatbelt无论exec策略怎么写都拒绝把setuid二进制exec进沙箱，这是平台限制不是配置选择。所有用Seatbelt的参考实现都一样。
- `sysctl-read`和`mach-lookup`还是全放行，剪贴板可读、Apple Events通道开着。
- `/private/var/folders`是整棵树可写，没有收窄到本进程的`TMPDIR`，所以在屏蔽清单生效之前读到的凭证仍然可以落在系统per-user缓存目录里，等一条不带沙箱的命令来取。
- git hook和git config在工作目录里可写。它们和agentics目录是同一形状的逃逸，作为`sandbox.deny_write`条目支持手动加上，但不是默认：实测禁掉`.git/hooks/**`会让`git init`和`git clone`直接失败，因为两者都要写这个目录。要关掉它得先有第5步的升级路径。
- 没有违规审计。被拒的操作只体现为命令自己的错误文本，没有任何地方记录沙箱拦了什么。

### 2. 开关

策略在包装命令的那一刻从`~/.openprogram/config.json`的`sandbox.*`读出来。`sandbox.mode`为`off`（默认值）时`resolve_policy()`返回`None`，否则返回一个`SandboxPolicy`。七个键注册在`openprogram/config_schema.py::SETTINGS`里，所以`openprogram config`、setup向导、TUI设置页和Web设置页都会渲染它们：

| 键 | 含义 | 默认 |
|---|---|---|
| `sandbox.mode` | `off`或`workspace-write` | `off` |
| `sandbox.writable_roots` | 工作目录之外还可写的目录 | `[]` |
| `sandbox.deny_read` | 沙箱内不可读的glob | §1.3那份凭证清单 |
| `sandbox.deny_write` | 沙箱内不可写的glob | `[]`，外加常开的agentics目录 |
| `sandbox.network` | 沙箱内是否有网络 | `false` |
| `sandbox.pass_env` | 额外透传的环境变量名 | `[]` |
| `sandbox.on_unavailable` | 平台工具缺失时`refuse`还是`warn` | `refuse` |

CLI REPL和Web UI的`/sandbox`都通过`set_setting`写`sandbox.mode`，所以这个开关是持久的，不是单次会话的。

**载体是文件，这正是关键。**开关原来挂在`ContextVar`上，而每一个新起上下文的边界都会把它弄丢。其中三个边界在真实调用路径上：Web UI在websocket的asyncio任务里设置，agent轮次跑在裸`threading.Thread`里；`openprogram/agent/process_runner.py`用`mp.get_context("spawn")`，spawn不携带上下文变量；嵌套CLI干脆是另一个进程。在每个交接点补`copy_context()`并不等价：spawn出来的子agent确实能靠它把开关带进worker线程，但在followup线程上照样掉回默认值，这是实测的。一个要跨进程存活的用户级设置，本来就不该挂在上下文变量上。同样挂在上下文变量上的那几个按链计数的执行态是另一回事，它们留在原处，在每个线程入口重新绑定。改完之后实测，Web那条形状里裸线程确实跑在沙箱内且看到空的`OPENAI_API_KEY`，spawn那条形状里的子进程同样如此。

**任何审批决定都掀不动它。**策略在backend里读，位置在权限层之下。`permission_mode="bypass"`把`_gated_execute`短路到工具自己的execute，而那次调用照样经过`_invocation`。实测：直接调bash工具的`execute`（bypass分支执行的就是这一句），写工作目录之外和读SSH私钥都失败。这不能替代权限层需要的那条硬底线（第5步），它的意思是bypass绕过的东西里不包括沙箱。

**平台工具缺失时默认拒绝执行。**`sandbox.mode`开着、平台工具缺失、`sandbox.on_unavailable`是`refuse`时，`_invocation`抛`SandboxUnavailable`，`LocalBackend.run`把它变成失败的`RunResult`，文案给出两条出路：装工具，或者把`sandbox.mode`设成`off`。`warn`恢复原来那种不受保护地跑掉的行为，附一行日志。

粒度仍然是整个安装一个设置：不分agent、不分工具、不分命令。`wrap_command`接收显式策略，手上有策略的调用方可以传进去，但目前没有任何地方按调用点给出不同的策略。

### 3. 覆盖面

全仓库约25处会把内容变成运行中的进程，走沙箱的有2处。

| 执行点 | 跑的是什么 | 命令来源 | 沙箱 |
|---|---|---|---|
| `functions/tools/bash/bash.py` → `backend/local.py::_invocation` | 任意shell串 | 模型 | **是**，需`sandbox.mode`打开且用local backend |
| `backend/docker.py`、`backend/ssh.py` | `docker run` / `ssh <target> "…"` | 模型 | 无 |
| `functions/tools/process/process.py` | 长驻`backend.spawn()` | 模型 | 跟随bash，同一个`_invocation` |
| `functions/tools/execute_code/execute_code.py` | `subprocess.run([python, script])`，脚本由模型写、解释器路径也是模型参数 | 模型 | 无，文件头明写隔离不是本工具的职责 |
| `functions/tools/cron/worker.py` | `Popen(command, shell=True, start_new_session=True)`和`python -m openprogram.cli deep-work` | 模型 | 无，脱离会话存活，触发时没有审批 |
| `memory/scriptorium/management/workspace.py` | `subprocess.run(command, shell=True, cwd=stage_dir)` | 模型，经MCP`shell`工具 | **是**，同一份策略，cwd是暂存目录 |
| `memory/scriptorium/agent_runtime/claude_code.py` | 嵌套Claude Code SDK agent，`permission_mode="dontAsk"` | 模型驱动 | 无 |
| `functions/_registry.py`的`exec_module`，由`functions/watcher.py`驱动 | 导入`functions/agentics/`下的`.py`，模块级代码在agent进程内执行 | 模型可写该目录 | 无；**沙箱内**的命令已经写不进去，沙箱外的还可以 |
| `webui/_functions.py` | 每次UI运行重新exec该模块 | 模型可写 | 无 |
| `agent/process_runner.py` | spawn子进程跑一个`@agentic_function` | 模型 | 里面的bash和别处一样解析策略 |
| `plugins/loader.py` | 进程内`importlib.import_module()` | 插件清单 | 无，`plugins/sandbox.py::load_subprocess`抛`NotImplementedError` |
| `mcp/client.py` | stdio MCP server，`env={**os.environ, …}` | 配置文件，bash可改写 | 无 |
| `webui/routes/mcp.py` | 直接从HTTP请求体拿命令起进程 | HTTP调用方 | 无，尽管docstring自称one-shot sandbox |
| `events/shell_hooks.py` | `subprocess.run(command, shell=True, input=<事件JSON>)` | 命令来自用户配置，stdin是模型内容 | 无，超时fail-open |
| `providers/_shared/cli_backend/runner.py` | claude/codex/gemini CLI | 配置加prompt | 无，且当前没有代码import这个模块 |
| grep、worktree、agent_browser、git管道 | 带`--`分隔的argv列表 | 模型参数 | 无沙箱，不可注入 |
| `_cli_cmds/*`、`plugins/installer.py` | `git clone`、`pip install`、`npm i -g` | 用户自己敲的CLI | 无，符合预期 |

其中三条要单独说。

**从模型输出到宿主代码的最短路径不经过bash。**`functions/watcher.py`每两秒轮询`functions/agentics/`并调`rescan()`，最终走到`spec.loader.exec_module(module)`。往那儿放一个`.py`，几秒内模块级代码就在agent进程里跑起来，不经工具审批。沙箱内的命令已经写不进这个目录，它是一条任何配置都删不掉的deny-write条目；但`write`工具不走沙箱，所以这条路径是被收窄而不是被关掉。

**spawn出来的子agent关掉了审批。**`openprogram/agent/sub_agent_run.py`给新建的轮次设`permission_mode="bypass"`，`_gated_execute`在第③步就因bypass短路，位置在会拦住bash/execute_code/process的`_RISKY_TOOLS`检查之前。规则层的deny和ask仍然生效，它们排在更前面。沙箱不受这条影响：它在这一层之下解析，被bypass的bash调用和被批准的一样会被包住（§2）。

**一个看起来在防护的参数并不防护。**`webui/_runtime_management.py`往`create_runtime()`传`full_auto=False, sandbox="read-only"`，而`providers/openai_codex/runtime.py`把这些kwarg明确记为接受即忽略。

---

## 第二部分：别人怎么做

`references/`下有八个框架，下面八个全部覆盖，包括四个完全不做沙箱的。不做隔离本身就是一种设计立场，而且各自都给出了替代方案。

计数上有两点要先说清。`pi-ai`不是独立框架：它是`pi-mono`同一上游的只读子集（`references/pi-ai/README.md:1-6`，两个仓库的remote都指向`badlogic/pi-mono`），只包含provider和协议层。另外四个系统级沙箱里有两个是同一份代码：`claude-code`和`pi-mono`的沙箱扩展都调用`@anthropic-ai/sandbox-runtime`，所以这一组里独立的系统调用级实现只有两份。

### 4. 四种立场

| 框架 | 系统级沙箱 | 机制 | 模型的命令在哪里跑 | 默认开 |
|---|---|---|---|---|
| `claude-code` | 有 | macOS用Seatbelt，Linux用bubblewrap加seccomp helper，实现在外部包`@anthropic-ai/sandbox-runtime` | 宿主，被包住 | 否（`sandbox.enabled`为false） |
| `codex-cli` | 有 | Seatbelt SBPL加bubblewrap加seccomp，实现在仓库内`codex-rs/` | 宿主，被包住 | **是**，`read-only` |
| `openclaw` | 有，但粒度更粗 | **Docker容器**，默认一个agent一个，走可插拔的backend注册表（`docker`/`ssh`/插件提供） | **容器内**，或远端主机 | 否（`sandbox.mode`为`"off"`） |
| `pi-mono` | 只有示例扩展里有 | `@anthropic-ai/sandbox-runtime`，通过替换`bash`工具实现接入 | 宿主，不装扩展就不包 | 核心里没有这个概念，扩展自身默认启用 |
| `hermes-agent` | 默认路径没有，可选后端有 | `TERMINAL_ENV`选`local`（默认）、`ssh`，或容器/远端后端（`docker`、`singularity`、`modal`、`daytona`、`vercel_sandbox`） | 默认宿主 | 不适用，默认就是宿主 |
| `opencode` | **没有，且文档明确列为非目标** | 无 | 宿主 | 无 |
| `weclaw` | **没有，而且它把被包的agent的沙箱也关掉了** | 无 | 宿主，经spawn出来的`claude`/`codex` | 无 |
| `pi-ai` | 不适用 | 完全没有执行面 | 无 | 无 |
| **OpenProgram** | 有 | Seatbelt和bubblewrap，仓库内 | 宿主，被包住 | 否（`sandbox.mode`为`off`） |

**`claude-code`**：证据取自本机安装的2.1.226二进制，因为`references/claude-code-leaked/src/utils/sandbox/sandbox-adapter.ts:17`只是从外部包import `SandboxManager`。策略是`(allow file-read*)`加空deny列表、写默认拒绝加显式allowlist加一份硬编码的dotfile与`.git` deny列表、`process-exec`完全不限制而靠子进程继承沙箱、网络走父进程里一个逐域名弹窗的代理。

**`codex-cli`**：这一组里唯一在仓库内实现的系统调用级沙箱。macOS上三份`.sbpl`拼装（`references/codex-cli/codex-rs/sandboxing/src/seatbelt.rs:21-24`），Linux上bubblewrap带`--new-session --die-with-parent --unshare-user --unshare-pid`再加seccomp（`linux-sandbox/src/bwrap.rs:318-332`、`landlock.rs:169-268`）。四档沙箱模式、四档审批模式、per-command策略。

**`openclaw`**：边界是Docker容器，不是系统调用过滤。`references/openclaw/src/agents/sandbox/backend.ts:43-94`是一张以全局Symbol为键的注册表，内置`docker`和`ssh`，`openshell`由插件注册；backend没注册就是硬拒绝。容器参数在`src/agents/sandbox/docker.ts:411-535`拼出。`src/agents/sandbox/config.ts`里的默认值：`readOnlyRoot: true`（`:108`）、`network: "none"`（`:110`）、`capDrop: ["ALL"]`（`:112`），另外无条件加`--security-opt no-new-privileges`（`docker.ts:488`）。但`sandbox.mode`本身默认`"off"`（`config.ts:246`）。

**`pi-mono`**：核心完全不设防。`references/pi-mono/packages/coding-agent/src/core/tools/bash.ts:79-85`就是一句`spawn(shell, [...args, command])`，带父进程完整环境，没有审批弹窗，文件工具也没有工作区约束。整个仓库找不到`--yolo`这类标志，因为没有东西需要绕过。隔离被交给用户侧，而且钩子是真的：`beforeToolCall`可以拦截或改写参数（`src/core/agent-session.ts:397-416` → `packages/agent/src/agent-loop.ts:581-604`），随包附带的示例`examples/extensions/sandbox/index.ts`直接把`bash`工具整个换掉。

**`hermes-agent`**：默认路径没有系统调用级沙箱。`references/hermes-agent/tools/terminal_tool.py:1013`读`TERMINAL_ENV`，默认`"local"`，而`tools/environments/local.py:493`就是以同一个OS用户跑`bash -c <模型给的串>`。替代方案是§6讲的三层命令守卫，外加可选的容器后端，容器后端下容器被明确宣布为边界、整个守卫层被跳过（`tools/approval.py:1052-1054`）。

**`opencode`**：`references/opencode/SECURITY.md:15-19`把立场写得很直白，权限系统是UX功能而不是安全隔离，需要真隔离就把opencode跑在容器或VM里；沙箱逃逸明确列为out of scope。替代方案是一张三效果规则表（`allow`/`ask`/`deny`），按工具名加资源pattern匹配，last-match-wins。

**`weclaw`**：这一组里唯一主动移除隔离的。它是一个微信侧的桥，spawn `claude`或`codex`：`references/weclaw/agent/acp_agent.go:502-506`发的是`"sandbox": "danger-full-access"`加`"approvalPolicy": "never"`，`:567-574`再以`sandboxPolicy: {"type": "dangerFullAccess"}`重复一遍，`:718-722`把每个`session/request_permission`都自动答成allow。它唯一的真边界是对外发送附件时的符号链接解析加路径包含检查（`messaging/attachment.go:51-75`），而这个检查锚定的根目录可以被一条聊天消息用`/cwd /`扩到全盘（`messaging/handler.go:646-663`）。

**`pi-ai`**：14个文件，没有工具层，没有`spawn`，没有`child_process`。没有可隔离的东西。列在这里是为了覆盖完整，不作为一个数据点。

### 5. 四个方向

| | `claude-code` | `codex-cli` | `openclaw` | `pi-mono`核心 | `pi-mono`加扩展 | `hermes-agent` | `opencode` | `weclaw` | **OpenProgram** |
|---|---|---|---|---|---|---|---|---|---|
| **读** | 整盘，deny列表出厂为空 | 整盘，deny-read引擎出厂为空 | 容器内只看得到两个挂载；**宿主**侧read工具不受限 | 不受限 | `denyRead`出厂装弹：`~/.ssh`、`~/.aws`、`~/.gnupg` | 不受限；文件工具有一份read-deny列表，代码自己注明"不是安全边界" | `*.env`走ask | 不受限 | 整盘减去一份**出厂装弹**的屏蔽清单；只管bash，文件工具不受限 |
| **写** | 默认拒绝加allowlist，另有硬编码的dotfile和`.git` deny | 默认拒绝，`.git`/`.codex`/`.agents`受保护 | 容器内工作区挂载默认`:ro`，除非`workspaceAccess: "rw"` | 不受限 | `allowWrite: [".", "/tmp"]`，`denyWrite`含`.env`、`*.pem`、`*.key` | 对`/etc`、`/boot`、docker socket等敏感路径拒写 | 只有规则 | 不受限 | cwd加临时目录，减去agentics目录 |
| **执行** | 不限制，子进程继承沙箱 | 不限制 | 容器内不限制；**argv解包器**在审批allowlist之前拦掉被混淆的调用 | 不限制 | 不限制 | 47条正则加一份掀不动的hardline列表 | 只有规则 | 不限制 | 不限制，子进程继承沙箱 |
| **网络** | 提示式代理，空白名单意味着每个域名都问 | 默认断，可配代理加域名白名单 | 默认`--network none`；`host`和`container:<id>`被拦 | 无 | 域名allow/deny列表，默认10个registry域名 | `--network=none`存在但**从来没被传进去**，出厂路径上不可达 | 不限制 | 不限制 | 两个平台都断 |
| **子进程环境变量** | 由runtime处理 | 可配置，默认不过滤 | **名字正则加取值启发式**，出厂装弹 | 全量继承 | 全量继承（扩展根本没传`env`） | **剥离，且名单从provider注册表推导** | 不过滤 | 全量继承 | 白名单，没见过的名字自动被丢掉，不需要更新清单 |

有两行要强调，因为它们推翻了只看两家时得出的结论。

**八家里有三家出厂就装了凭证屏蔽。**`openclaw`拒绝把`.aws`、`.cargo`、`.config`、`.docker`、`.gnupg`、`.netrc`、`.npm`、`.ssh`以及`/etc`、`/proc`、`/sys`、`/dev`、`/root`、`/boot`和docker socket的各种别名作为bind挂载源（`src/agents/sandbox/validate-sandbox-security.ts:23-49`），并用兜底正则`/_?(API_KEY|TOKEN|PASSWORD|PRIVATE_KEY|SECRET)$/i`拦掉凭证形状的环境变量（`src/agents/sandbox/sanitize-env-vars.ts:1-19`）。`hermes-agent`的子进程环境黑名单是从provider注册表推导的而不是硬编码的（`tools/environments/local.py:78-99`），所以加新provider时不会漂移。`pi-mono`的沙箱扩展出厂就带着填好的`denyRead`和`denyWrite`，且`enabled: true`（`examples/extensions/sandbox/index.ts:55-77`）。出厂为空的那两家，正好就是最先调研的那两家。我们现在也出厂装弹，环境变量那一层用的是白名单而不是推导出来的黑名单：明天新增的provider会被自动丢掉，没人需要更新任何东西，`openclaw`那条兜底名字pattern留在下面，作为唯一能往回加名字的那个键的底线。

**三家各自都是部分覆盖，而且都把没覆盖的部分写清楚了。**`openclaw`的deny列表管的是挂载源，不管宿主的`read`工具，后者的`tools.fs.workspaceOnly`默认`false`。`pi-mono`的`denyRead`只对bash生效，不管它自己的`read`/`grep`/`find`。`hermes-agent`把这条写进了源码：`agent/file_safety.py:167-171`明说terminal工具以同一个用户身份运行、照样能`cat`出来，所以read-deny是纵深防御而不是边界。这里的结论不是部分覆盖可以接受，而是deny列表该放在每条路径都要过的那一层，并且没覆盖到的路径必须点名。

### 6. 粒度、审批与降级

| | 粒度 | 配置入口 | 沙箱与审批的关系 | 不可用时 |
|---|---|---|---|---|
| `claude-code` | 全局开关×单命令opt-out | 分层settings.json，没有CLI flag | 沙箱内的bash**免审批**；prompt教模型被拒时用`dangerouslyDisableSandbox`重试，从而触发弹窗 | 照跑但启动时明确告警；`failIfUnavailable`可改成硬失败 |
| `codex-cli` | per-command，工具可覆盖 | config.toml加`--sandbox`/`--add-dir`/`--yolo` | 沙箱内被拒→问用户→不带沙箱重跑，前面有五道闸 | 静默`None`；Windows降级成只读；WSL1硬报错 |
| `openclaw` | 全局×per-agent×per-tool×per-session（`mode: "non-main"`） | zod schema加生成的JSON schema，`agents.defaults.sandbox.*`和`agents.list[].sandbox.*` | **互不相干**：`host === "sandbox"`直接跳过审批allowlist，容器就是边界；`host === "gateway"`才走allowlist | **硬拒绝**并给出可操作的提示，另有`doctor`提前预警 |
| `pi-mono` | 整工具级allowlist（`--tools`），per-session | `~/.pi/agent/settings.json`；扩展自带一份JSON，**项目配置覆盖全局配置** | 核心没有；示例扩展也不弹窗 | 四条路径各自把`sandboxEnabled`置false后继续，包括"平台不支持" |
| `hermes-agent` | per-pattern审批，按上下文分策略（cron、subagent、gateway、CLI、oneshot） | `cli-config.yaml`、`approvals.mode`、`command_allowlist`、`TERMINAL_ENV` | 守卫**就是**审批系统；容器后端整体跳过 | 非交互、oneshot、batch三条路径fail-open；subagent和hardline列表fail-closed |
| `opencode` | per-tool×per-resource | opencode.json加agent frontmatter | 只有审批，没有沙箱 | 无 |
| `weclaw` | 无 | `~/.weclaw/config.json`，没有任何安全相关的键 | 审批被自动答成allow | 无 |
| **OpenProgram** | 整个安装一个设置 | `config_schema.SETTINGS`里七个`sandbox.*`键 | **不联动** | **硬拒绝**并给出两条出路，另有`warn`可选 |

最后一列的分布是最有用的部分。`openclaw`在Docker缺失时直接拒绝运行，并给出两条出路（`src/agents/sandbox/docker.ts:324-333`），镜像缺失时也拒绝拿一个通用镜像顶替。`claude-code`是照跑，但它自己的源码写清了为什么必须出声：修过的那个bug是`isSandboxingEnabled()`在依赖缺失时静默返回false，注释写的是"This is a security footgun — users configure allowedDomains expecting enforcement, get none."我们原来的行为正是那个bug描述的状态，现在`sandbox.on_unavailable`默认是`refuse`。

### 7. 前两轮没见过的招数

有十一种机制出现在这一轮新看的五个框架里，而在`claude-code`和`codex-cli`里都不存在。

**带语义透明性判定的argv解包**（`openclaw`，`src/infra/dispatch-wrapper-resolution.ts:351-383`）。一张18个启动器程序的表，包括`arch`、`caffeinate`、`chrt`、`doas`、`env`、`ionice`、`nice`、`nohup`、`sandbox-exec`、`script`、`setsid`、`stdbuf`、`sudo`、`taskset`、`time`、`timeout`、`xcrun`，从argv前面剥掉，好让审批allowlist匹配到真正的可执行文件。没有unwrap函数的条目（`sudo`、`doas`、`setsid`、`chrt`、`ionice`、`taskset`）直接拦死。可能透明的条目，只在实际用到的flag不改变语义时才放行：`nice -n 5`过，`env FOO=bar`不过，`arch -e`不过，`arch`和`xcrun`只在darwin上算透明。链深度上限4，溢出即拦（`:503-512`）。收益在`src/infra/exec-approvals-allowlist.ts:968-971`：wrapper链被拦的命令**没法为它保存一条"永远允许"的规则**。

**模式匹配之前先做反混淆归一化**（`hermes-agent`，`tools/approval.py:452-467`）。完整的ECMA-48 ANSI转义剥离、空字节剥离、Unicode NFKC归一化，所以全角的`ｒｍ　-ｒｆ　/`过不了正则。配套的还有`:136-146`的macOS `/private/*`软链镜像处理，`/private/etc/sudoers`按`/etc/sudoers`匹配。

**一条掀不动的底线**（`hermes-agent`，`tools/approval.py:198-220`，判定在`:1060-1063`）。删根、`mkfs`、`dd`写裸块设备、fork炸弹、`kill -1`和各种关机命令，在yolo模式、`approvals.mode=off`和cron approve-mode被读取**之前**就拦掉。拒绝文案自己写着："cannot be executed via the agent — not even with --yolo, /yolo, approvals.mode=off, or cron approve mode"。`:163-165`给的理由是：选择yolo意味着信任agent处理你的文件，不等于信任它抹盘。

**把猜密码当攻击而不是当权限问题**（`hermes-agent`，`tools/approval.py:238-266`）。没配`SUDO_PASSWORD`时出现`sudo -S`，无条件拦下，因为往sudo的stdin里灌东西只有试密码这一个用途。

**自保护模式**（`hermes-agent`，`tools/approval.py:360-375`）。agent不能停掉或重启自己的gateway，不能跑`hermes update`，不能`pkill hermes`，连`kill $(pgrep …)`和反引号形式这类结构变体也一并拦。

**按上下文分审批策略**（`hermes-agent`）。五种上下文五种策略：cron默认deny（`cron_mode`），subagent默认自动deny并留审计行（`tools/delegate_tool.py:73-84`，理由是工作线程里的`input()`会把父进程TUI卡死），gateway走异步队列，CLI走同步弹窗，oneshot直接开yolo因为弹窗会永远挂着（`hermes_cli/oneshot.py:170-172`）。

**拦掉嵌套agent自带的执行面**（`openclaw`，`extensions/codex/src/app-server/sandbox-guard.ts`）。openclaw驱动嵌套的Codex app-server时，把每个JSON-RPC方法分成`allowed-control-plane`、`blocked-native-bypass`、`requires-openclaw-environment`三类，拦掉`command/`、`fs/`、`windowsSandbox/`三个前缀（`:62`），并注入`sandbox_exec`和`sandbox_process`两个替代工具把操作绕回openclaw自己的沙箱。这是"嵌套agent自带的文件工具够不着"这个问题的通用答案。

**用策略哈希驱动沙箱重建，并带epoch**（`openclaw`，`src/agents/sandbox/config-hash.ts:37-70`）。把归一化后的有效策略算成SHA-256，作为label打在容器上，复用时再校验，不一致就强制重建。几个具名epoch常量（`SANDBOX_DOCKER_EXPLICIT_ENV_POLICY_EPOCH`、`SANDBOX_MOUNT_FORMAT_VERSION`）让策略**语义**的变化不需要改配置就能让在跑的沙箱失效。

**给自己的安全配置做静态检查**（`openclaw`，`src/security/audit-extra.sync.ts`）。一批具名检查项，包括`sandbox.docker_config_mode_off`、`sandbox.dangerous_bind_mount`、`sandbox.dangerous_network_mode`、`sandbox.dangerous_seccomp_profile`、`sandbox.dangerous_apparmor_profile`，还有专门覆盖`tools.exec.host="sandbox"`而`sandbox.mode="off"`这种情况的`tools.exec.host_sandbox_no_sandbox_defaults`，跑在用户配置上并给出报告。破窗用的开关是一个有类型的枚举集合（`DANGEROUS_SANDBOX_DOCKER_BOOLEAN_KEYS`，`src/agents/sandbox/config.ts:31-35`），每一个都配了一条对应的检查项。

**资源限额与容器生命周期**（`openclaw`和`hermes-agent`）。`openclaw`把`pidsLimit`、`memory`、`memorySwap`、`cpus`、`gpus`、`ulimits`都开了出来（默认全部不设），并按idle小时数加最长存活天数回收容器（`src/agents/sandbox/prune.ts:24-36`，24小时/7天）。`hermes-agent`硬编码了`--pids-limit 256`、给`/tmp`、`/var/tmp`、`/run`挂限定大小的`nosuid` tmpfs、`--cap-drop ALL`后再加回三个cap、`--security-opt no-new-privileges`、用`--init`当PID 1（`tools/environments/docker.py:161-171`），另有600秒前台上限和50KB输出上限。`claude-code`和`codex-cli`除了`RLIMIT_CORE=0`之外没有任何资源限额。

**拦截器崩了就fail-closed，非交互即拒**（`pi-mono`）。`beforeToolCall`处理器抛出非`Error`时会被转成`Extension failed, blocking execution`（`src/core/agent-session.ts:410-415`）。随包的permission-gate示例在没有UI时直接拦（`examples/extensions/permission-gate.ts:20-23`），这是CI下的正确默认值，而且比应该有的少见。注意它有不对称：`user_bash`那条路径会吞掉处理器的错误并继续执行。

同一轮还有三条反面案例值得记下来，因为我们有对应的面。

**项目配置覆盖全局沙箱配置且没有信任确认**（`pi-mono`，`examples/extensions/sandbox/index.ts:79-102`）。`deepMerge(deepMerge(DEFAULT_CONFIG, global), project)`意味着一个clone下来的仓库只要带上`.pi/sandbox.json`写`{"enabled": false}`，就能关掉用户全机生效的沙箱。项目级扩展（`.pi/extensions/`）同样在进程内自动加载，没有信任闸。

**截断之后把未截断的副本交给模型**（`pi-mono`，`src/core/tools/bash.ts:360-364`）。输出按2000行/50KB截断，全文写进`/tmp/pi-bash-*.log`并把路径返回给模型，模型`cat`一下就拿回来了。

**聊天桥把审批环路化掉**（`weclaw`）。`hermes-agent`的ACP适配层把权限请求转发给客户端（`acp_adapter/permissions.py:22-28`），`weclaw`则自己替客户端答了。同一个协议，相反的极性。而且`weclaw`没有发送者allowlist：`messaging/handler.go:261-409`只按消息类型过滤并按message id去重，从不检查是谁发的。

### 8. 凭证这条

只看两家时的结论是：出厂都不屏蔽凭证读取，两家都在外传侧收口，一家把流量走逐域名弹窗的代理，一家默认断网。八家都看过之后这个结论要收窄：**出厂带空deny列表的是少数派**，出厂装弹的三家分别装在不同的层（挂载源、子进程环境、只对bash生效的路径glob），而且没覆盖的部分是写出来的而不是藏起来的。

不管怎样，那条让空deny列表站得住的推理在这里不成立。出站网络本来就断，比八家中任何一家都严；但memory写入器是一条不碰网络的外传通道：它在暂存目录里跑shell命令，产出提交进记忆库，记忆库内容又会在之后的会话里回到上下文。`cat ~/.openprogram/auth/*/default.json > topics/x.md`全程离线就完成了外传。所以deny-read在这里是必需项，在那边是可选项。

---

## 第三部分：我们计划怎么做

### 9. 缺口、先例、步骤

第一部分实测出的每条缺口，第二部分里已经解决它的那一家，以及§10里补它的那一步。

| 缺口（第一部分） | 谁解决了、怎么解决（第二部分） | 步骤 |
|---|---|---|
| macOS上`/dev/null`不可写 | `codex-cli` `seatbelt_base_policy.sbpl:18-21`；`claude-code`用`require-all`加`vnode-type CHARACTER-DEVICE` | 1 |
| exec白名单误伤git、python、node | 两家都放开`process-exec`、靠子进程继承沙箱；`openclaw`改成限制**混淆**而不是限制路径 | 1 |
| Linux上tmpfs盖掉`/tmp`下的工作目录 | 没人有这个bug，它是挂载顺序问题 | 1 |
| 整盘可读，没有deny-read引擎 | `openclaw`的挂载源deny列表、`hermes-agent`的注册表推导环境变量剥离、`pi-mono`扩展的`denyRead`，**八家里有三家出厂装弹** | 2 |
| 环境变量全量继承 | `hermes-agent` `local.py:78-99`从provider注册表推导黑名单；`openclaw` `sanitize-env-vars.ts:1-19`按名字pattern加取值启发式匹配 | 2 |
| Linux共享PID命名空间，宿主进程可读可杀 | `codex-cli`和`claude-code`都传`--unshare-pid`；`claude-code`还把`signal`和`process-info*`限定在`(target same-sandbox)` | 2 |
| 开关在线程、spawn、嵌套CLI三个边界上丢失 | `codex-cli`每次exec重建argv；`openclaw`在每个调用点解析策略 | 3 |
| 没有配置面 | 每个有沙箱的框架都有；`openclaw`从zod生成JSON schema | 3 |
| 不可用时静默放行 | `openclaw`硬拒绝并给出可操作提示，另有`doctor`提前预警；`claude-code`的源码把静默那版称为security footgun | 3 |
| 默认关，开了又不能用 | `codex-cli`默认就开`read-only` | 4 |
| 沙箱和审批不联动 | `claude-code`在沙箱内免审批；`codex-cli`失败后升级出沙箱；`openclaw`认为容器已经够了，直接跳过allowlist | 5 |
| `permission_mode="bypass"`在risky工具检查之前短路 | `hermes-agent`把hardline列表放在**所有**bypass之下 | 5 |
| 按前缀匹配的命令allowlist可被wrapper和Unicode绕过 | `openclaw`的argv解包器、`hermes-agent`的NFKC加ANSI归一化 | 5 |
| cron worker触发时没有审批，子agent关掉审批 | `hermes-agent`的按上下文策略：cron默认deny，subagent自动deny并留审计行 | 5 |
| 嵌套Claude Code CLI的文件工具够不着 | `openclaw`拦掉嵌套agent的`command/`和`fs/`方法，注入绕回自己沙箱的替代工具 | 接memory |
| 没有违规审计 | `claude-code`把内核deny行归因到具体命令并喂回模型；`openclaw`在专用的`agents/tool-policy` logger上记录策略判定 | 跟第2步一起 |
| 没有资源限额 | `hermes-agent`的`--pids-limit 256`、限定大小的tmpfs、600秒前台上限；`openclaw`开出`pidsLimit`/`memory`/`cpus`/`ulimits` | 后补 |
| 配置文件没有写保护 | `claude-code`显式deny掉所有settings.json，理由直接写着防逃逸；`codex-cli`保护`.codex`/`.git`/`.agents` | 跟第2步一起 |
| 没有对自己沙箱设置的静态检查 | `openclaw` `src/security/audit-extra.sync.ts`的具名检查项 | 后补 |

### 10. 修复顺序

五步，按依赖排。每一步都是下一步能产生价值的前提。

**1. 可用性，已完成。**`process-exec`不再限制，子进程继承profile，所以`git`、`python3`、`make`、`clang`、conda python以及`/sbin`和`/usr/sbin`下的东西都能跑。`/dev/null`、`/dev/zero`、`/dev/random`、`/dev/urandom`、`/dev/tty`通过`require-all`加`vnode-type CHARACTER-DEVICE`可读可写，`2>/dev/null`正常。Linux上`--tmpfs /tmp`发在cwd bind之前，工作目录落在`/tmp`下也不会消失。macOS上仍然挡着的是`ps`和`top`，因为Seatbelt根本不允许把setuid二进制exec进沙箱。

**2. 凭证屏蔽，已完成。**deny-read清单出厂装弹（§1.3）。macOS上每条glob同时发`deny file-read*`和`deny file-write-unlink`，被禁读的路径不能用删除操作反推存在性；Linux上目录用`--perms 0000 --tmpfs`、文件用`--ro-bind /dev/null`屏蔽，宿主上不存在的路径跳过，因为只读的根让bubblewrap没地方创建挂载点。子进程环境变量用白名单而不是从provider注册表推导的黑名单：推导出来的名单要跟着注册表一起重建，白名单会自己丢掉没见过的名字，`openclaw`那条兜底名字pattern留作`sandbox.pass_env`的底线。Linux加上`--unshare-pid`，否则刚从子进程里去掉的key又能从`/proc/<agent_pid>/environ`读回来。deny-write覆盖agentics目录且任何配置都删不掉；git hook和git config是同一形状的逃逸，但保持opt-in，因为禁掉`.git/hooks/**`会让`git init`和`git clone`失败，而在第5步之前没有升级路径。

**3. 开关语义，已完成。**`ContextVar`已经删掉。策略在包装命令的那一刻从配置里的`sandbox.*`解析，asyncio任务到线程、spawn子进程、嵌套CLI三个边界都扛得住，因为文件不属于任何上下文。它同时坐在权限层之下，所以`permission_mode="bypass"`短路掉的是审批卡而不是沙箱。`wrap_command`接收显式策略供手上有策略的调用方使用；目前还没有按工具或按调用点给出不同策略的地方，那正是"调用点可覆盖"原本要买到的东西。平台工具不可用时默认拒绝执行，并给出两条出路。

**4. 默认开**，`workspace-write`语义，和`codex-cli`的立场一致。**不做的后果**：一个默认关又没有配置项的机制在真实使用中不会运行，前三步全都无从检验。

**5. 和审批联动**，三个方向而不是两个。

*正向*：一条将在沙箱内执行、策略未被放宽的bash命令跳过审批卡，开沙箱对用户变成少点几次确认。这是`claude-code`的`autoAllowBashIfSandboxed`，`openclaw`走得更远，凡是在容器里跑的东西整个跳过allowlist。

*反向*：沙箱内被拒的命令带上原因发起审批，批准后不带沙箱重跑，这是域名白名单的廉价替代品，`pip install`、`npm i`、`git fetch`都走这条路解决。

*向下，已完成*：一条任何bypass都掀不动的底线，加上按上下文的默认值。`_hard_constraint_violation`跑在`_gated_execute`最前面，排在规则层和`permission_mode="bypass"`短路之前，所以无论是存下来的allow规则还是`sub_agent_run.py`设的那个模式，都掀不动它。`agent_spawn`轮次直接被拒掉`bash`/`exec`/`shell`/`execute_code`/`process`，`write`/`edit`/`apply_patch`只能落在本轮工作目录之内。配套的按上下文默认值一并生效：cron worker无人值守触发、没有审批路径，所以`cron`轮次只剩只读工具，两种非交互来源都直接拒绝而不是挂在审批卡上，理由很具体，工作线程里弹窗会死锁。最后，在信任任何命令pattern规则之前，包括今天的`SAFE_AUTO_ALLOWLIST`前缀匹配，先按`hermes-agent`的做法归一化命令（剥ANSI、剥空字节、NFKC），再按`openclaw`的做法解包argv，遇到无法证明透明的启动器就拦而不是猜。对原始字符串做前缀匹配，被`env X=1 <cmd>`和全角字符两招就破了。

权限规则和沙箱策略还应共用一个来源：用户写的`deny: Read(~/.ssh/**)`同时成为沙箱的deny-read条目。

**不做的后果**：沙箱纯粹是额外负担，审批一次不少、能跑的命令变少，没人会打开它；而且几条bypass路径会继续绕过沙箱做的任何事。

配套的profile修补清单已经缩到剩下的部分。已完成：工作目录拼进profile前先转义，放开exec之前补上了`(allow signal (target same-sandbox))`和`(allow process-info* (target same-sandbox))`，Linux传`--new-session --die-with-parent --unshare-pid --unshare-ipc --unshare-uts --cap-drop ALL`，两条沙箱路径统一用`/bin/bash`。仍然敞着：`sysctl-read`和`mach-lookup`收成具名白名单，现在的全放行让剪贴板可读、Apple Events通道开着；`/private/var/folders`收窄到本进程的`TMPDIR`而不是整棵树。`--unshare-user`不在清单里：非setuid的bubblewrap构建自己就会建用户命名空间，setuid的构建则不接受这个参数。

有两条后补项现在有了可以点名的先例。资源限额：`hermes-agent`硬编码`--pids-limit 256`和限定大小的`nosuid` tmpfs，并把前台命令截在600秒，这比上cgroups便宜得多。配置静态检查：`openclaw`对自己的沙箱设置跑具名检查项，其中一条覆盖的正是我们还留着的那类缺陷，`webui/_runtime_management.py`传的`sandbox="read-only"`进了一个把该参数标注为忽略的runtime。

### 接memory写入器

memory写入器有两个执行面，`wrap_command`只够得着其中一个。

MCP`shell`工具（`memory/scriptorium/management/tools.py` → `workspace.shell()`）在OpenProgram进程内跑`subprocess.run(command, shell=True, cwd=stage_dir)`。它现在解析同一份策略，用暂存目录作为工作目录包住命令，所以bash工具在沙箱内时它也在。

Claude Code CLI子进程（`memory/scriptorium/agent_runtime/claude_code.py`）是另一回事。它的`Read`/`Write`/`Edit`/`Grep`/`Glob`在CLI进程内部执行，`wrap_command`碰不到，而`permission_mode="dontAsk"`把它自己的审批也关了。这个CLI进程也不该被包进沙箱：它要调Anthropic API，而沙箱没有网络。

CLI进程仍不进入OS沙箱，因为它需要访问Anthropic API。它自带的`Read`、`Write`、`Edit`、`Grep`、`Glob`和`Bash`现在被显式禁用。OpenProgram通过MCP提供五个文件操作的替代工具：路径必须位于暂存工作区内，`sources/`下的写入会被拒绝，每次结果都记入写入器审计。MCP `shell`替代工具调用`LocalBackend._invocation(..., force_sandbox=True)`；平台沙箱不可用时直接拒绝执行。

当前边界是：**嵌套CLI可以访问模型API，但不能调用自带的文件或命令工具；暴露给它的文件和命令操作全部由宿主MCP执行，命令强制经过OS沙箱。**

它挡住的威胁是具体的，不是假想的。只要挂了消息渠道，进入写入器prompt的文本就是攻击方可影响的：入站消息正文里带着发信人自己在平台上设的显示名。从那里被执行的命令本可以读`~/.openprogram/auth/*/default.json`并写进某个topic文件，而记忆库内容会回到之后会话的上下文里，这是一条不碰网络的外传路径，也正是"网络已经断了"没能覆盖的部分。

---

## 实现状态

已落地：

- §1和§2描述的是当前macOS与Linux实现，修复顺序第1、2、3步已完成。
- `_hard_constraint_violation`排在权限规则和`permission_mode="bypass"`之前。`agent_spawn`轮次不能调用`bash`/`exec`/`shell`/`execute_code`/`process`，也不能写到工作目录之外；`cron`轮次只允许只读工具。非交互请求在需要审批时立即拒绝。
- `write`、`edit`和`apply_patch`检查写入路径，始终拒绝`functions/agentics/`自动导入目录和owner来源登记文件。运行时只导入由`openprogram programs install`登记的路径；agentic子进程接收父进程有效沙箱策略的序列化副本。
- memory写入器禁用嵌套CLI自带的文件与命令工具，改用受管MCP替代工具，并强制MCP `shell`经过OS沙箱。本地一次性MCP测试同样强制使用沙箱且只接受回环请求；浏览器请求还必须同源。

未实现：

- 第4步（默认开），以及第5步的正向和反向两条（沙箱内命令跳过审批卡；沙箱内被拒的命令升级成不带沙箱重跑）。
- 按工具、按调用点的策略。`wrap_command`接收显式策略，但每个调用方解析的都是同一份配置。
- memory写入器受管文件工具与通用`file.changed`/沙箱事件的完全一致性；路径检查和写入器审计已经实现。
- 现有`SAFE_AUTO_ALLOWLIST`前缀匹配之前的命令归一化和argv解包。
- 违规审计、资源限额、配置静态检查、Windows支持。
- `sysctl-read`和`mach-lookup`的具名白名单，以及把`/private/var/folders`收窄到`TMPDIR`。
- git hook和git config的写保护，现在是opt-in而不是默认。

已知限制，全部实测：

- `ps`和`top`在Seatbelt沙箱里跑不了。它们是setuid，平台无论exec策略怎么写都拒绝把setuid二进制exec进沙箱。
- Linux上，中间带通配符的deny-read glob（比如`**/.env`）没有对应实现：bubblewrap是把路径盖掉而不是做匹配，所以这类pattern在Linux被丢弃，只在macOS生效。
- §3的执行点清单尚未全部处理。本批次不修改`execute_code`和通用Web重载执行。cron direct执行已经固化策略并使用`force_sandbox=True`；函数自动导入现在只接受owner登记的程序来源。
- `program-sources.json`出现之前已存在的树内Harness默认不再导入，owner需要重新运行`openprogram programs install <名称或URL>`完成登记。同一命令可以登记现有开发symlink，不修改symlink目标。
