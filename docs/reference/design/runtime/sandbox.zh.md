# 沙箱（Sandbox）

沙箱是**宿主原生进程隔离层**：macOS使用Seatbelt，Linux使用bubblewrap，限制子进程的文件系统、进程视图、环境变量和网络。写死的`owner`/`paired`权限档常量表、权限规则与owner精确审批决定一项操作是否可以尝试；`SandboxPolicy`决定获准进程实际可以访问什么；hard constraints先于两者执行（见[`permission-model.md`](permission-model.md) §1.1）。获批重试仍在OS沙箱内执行，不能取消凭证过滤或hard floor。

同一份内容的图示版在[`sandbox-architecture.html`](sandbox-architecture.html)。

**本文分三层。**[第一部分](#第一部分我们现在怎么做)是当前实现与实测结果。[第二部分](#第二部分别人怎么做)分析`references/`下八个框架，包括明确不做沙箱的框架。[第三部分](#第三部分实施决策与记录)记录采用的设计、参考来源和实施顺序。

---

## 第一部分：我们现在怎么做

策略实现位于`openprogram/sandbox/__init__.py`。公开接口包括`SandboxPolicy`（冻结dataclass）、`resolve_policy()`、`is_available()` / `unavailable_reason()`、`child_env(policy)`、`validate_write_path()`和`wrap_command(command, cwd, policy) -> (args, shell)`。`openprogram/backend/local.py::_invocation`是共用命令边界，bash、process、本地`execute_code`、cron direct、memory写入器MCP `shell`和one-shot MCP启动都使用它。spawn的agentic子进程显式接收策略快照。`write`、`edit`和`apply_patch`不需要转成shell命令，而是直接执行相同的可写根与hard-floor检查。

本地后端确定使用宿主原生沙箱，使命令使用宿主实际安装的Git、Python、Conda、npm、编译器和项目环境。Docker不是本地沙箱实现，也不是自动回退。现有Docker与SSH执行后端分别声明容器或远端主机为边界；只有出现需要用户显式选择独立Linux环境的明确需求后，才另行设计Docker沙箱后端。

### 1. 边界

四个方向，不对称：读除凭证glob之外不限，写限制在工作目录，执行不限制，网络两个平台都断。这一节全部是在已发布代码上实测的。

#### 1.1 macOS：Seatbelt

`wrap_command`返回`/usr/bin/sandbox-exec -p <profile> /bin/bash -c <command>`，profile由`_seatbelt_profile()`内联生成：

| 资源 | 策略 |
|---|---|
| 兜底 | `(deny default)` |
| 文件读 | `(allow file-read* (subpath "/"))`，之后每条deny-read glob各发一条`deny file-read*`正则 |
| 文件写 | cwd、额外的`writable_roots`、当前进程`TMPDIR`、`/private/tmp`、`/tmp`，之后每条deny-write glob各发一条`deny file-write*`正则 |
| 删除被禁路径 | 每条deny-read glob同时发`deny file-write-unlink`，被禁读的路径不能用删除操作反推存在性 |
| 进程执行 | `(allow process-exec)`，不限制，子进程继承profile |
| fork | 允许 |
| 信号、进程信息 | 只限`(target same-sandbox)` |
| POSIX信号量与共享内存 | 允许，给Python multiprocessing用 |
| 字符设备 | `/dev/null`、`/dev/zero`、`/dev/random`、`/dev/urandom`、`/dev/tty`的读写和ioctl，每条都用`require-all`加`vnode-type CHARACTER-DEVICE` |
| sysctl | 硬件名字前缀，以及`kern.hostname`、`kern.osrelease`、`kern.ostype`、`kern.version` |
| Mach IPC | 不提供通用`mach-lookup` |
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

可用性按实际执行能力判断，不只检查PATH中是否存在文件。Linux针对每个`bwrap`可执行文件的首次检查会用策略要求的PID、IPC、UTS、网络、挂载和capability限制执行`/bin/true`。因此，已经安装`bwrap`但禁止非特权用户命名空间的宿主会被判为不可用，并按`sandbox.unavailable_policy`处理；普通宿主执行不会被标记为已进入沙箱。

#### 1.3 屏蔽清单里有什么

出厂清单不是空的：`~/.ssh/**`、`~/.aws/**`、`~/.gnupg/**`、`~/.openprogram/auth/**`、`~/.claude.json`、`~/.claude/.credentials.json`、`~/.config/gh/**`、`~/.netrc`、`~/Library/Keychains/**`、`**/.env`。启用沙箱后的实测结果是：具体凭证路径在macOS上报`Operation not permitted`，在Linux上报`Permission denied`；`rm -f ~/.ssh/id_ed25519`同样被拒，不会泄露文件是否存在。`**/.env`这种中段通配只在macOS正则profile中可强制执行。Linux上的敏感内容必须配置精确路径，或`/absolute/path/to/secrets/**`这类具有确定前缀的目录级deny；bubblewrap不能实现全文件系统中段通配匹配。

清单之外仍然全盘可读。这是有意选的姿态而不是疏漏：全盘读是命令了解自己所在系统的方式，收口收在携带凭证的路径上，不是收在读这个动作上。

#### 1.4 子进程的环境变量

沙箱内的子进程拿到的是一份白名单：`PATH`、`HOME`、`SHELL`、`USER`、`LOGNAME`、`TERM`、`TMPDIR`、`TMP`、`TEMP`、`TZ`、`PWD`、`OLDPWD`、`LANG`、`LANGUAGE`、`COLUMNS`、`LINES`和`LC_*`，再加上`sandbox.pass_env`里名字本身不像凭证的条目。实测：父进程里164字符的`OPENAI_API_KEY`到子进程是空串，`env | grep -iE '(key|token|secret|password)='`没有输出。

选白名单而不是从provider注册表推导的黑名单，理由只有一条：明天新增的provider会被自动丢掉，没人需要更新任何东西，而推导出来的名单要跟着注册表一起长。凭证名字pattern留作`sandbox.pass_env`的底线，避免这个逃生口顺手把key发给每一条命令。

Linux上光洗环境变量不够。没有`--unshare-pid`时，`/proc/<agent_pid>/environ`会把刚从子进程里去掉的key还回来。加上之后，沙箱内的进程只看得到4个PID，`cat /proc/<宿主pid>/environ`报"No such file or directory"，`kill -9 <宿主pid>`报"No such process"，宿主进程照常活着。

#### 1.5 已知行为与平台限制

- macOS上`ps`和`top`跑不了。它们是setuid二进制，Seatbelt无论exec策略怎么写都拒绝把setuid二进制exec进沙箱，这是平台限制不是配置选择。所有用Seatbelt的参考实现都一样。
- git hooks和仓库config在工作目录里可写。owner可以将其加入`sandbox.deny_write`，但默认不禁止：实测禁止`.git/hooks/**`会让`git init`和`git clone`失败，因为两者都要写该目录。这是已经记录的兼容性选择。
- 带平台错误文本的沙箱拒绝会产生结构化`sandbox.violation`事件。`pbpaste`这类无错误文本的服务拒绝保持失败，不提供升级。

### 2. 开关

策略在包装命令的那一刻从`~/.openprogram/config.json`的`sandbox.*`读出来。新安装默认`workspace-write`；已有配置中显式的`danger-full-access`保持不变。七个键注册在`openprogram/config_schema.py::SETTINGS`里，所以`openprogram config`、setup向导、TUI设置页和Web设置页都会渲染它们：

| 键 | 含义 | 默认 |
|---|---|---|
| `sandbox.mode` | `danger-full-access`或`workspace-write` | `workspace-write` |
| `sandbox.writable_roots` | 工作目录之外还可写的目录 | `[]` |
| `sandbox.deny_read` | 沙箱内不可读的glob | §1.3那份凭证清单 |
| `sandbox.deny_write` | 沙箱内不可写的glob | `[]`，外加常开的agentics目录 |
| `sandbox.network` | 沙箱内是否有网络 | `false` |
| `sandbox.pass_env` | 额外透传的环境变量名 | `[]` |
| `sandbox.unavailable_policy` | 平台后端缺失或无法创建所需隔离时`refuse`还是`warn` | `refuse` |

CLI REPL和Web UI的`/sandbox`都通过`set_setting`写`sandbox.mode`，所以这个开关是持久的，不是单次会话的。

**开关使用持久化配置。**开关原来使用`ContextVar`，每一个新上下文边界都会丢失该值。其中三个边界位于实际调用路径：Web UI在websocket的asyncio任务里设置，agent轮次运行于普通`threading.Thread`；`openprogram/agent/process_runner.py`使用`mp.get_context("spawn")`，spawn不携带上下文变量；嵌套CLI是独立进程。在每个交接点增加`copy_context()`也不等价：spawn子agent可以把开关传入worker线程，但实测followup线程仍会恢复默认值。按调用链计数的执行状态继续使用上下文变量，并在每个线程入口重新绑定；安装级策略不使用上下文变量。修改后实测，Web worker线程在沙箱内执行并看到空的`OPENAI_API_KEY`，spawn子进程也得到相同结果。

**只有本地interactive owner可以申请一次精确重试并放宽可配置限制。**重试仍使用OS沙箱，凭证环境过滤和不可配置的agentics禁写保持生效；cron、subagent和paired渠道不能使用该路径。`permission_mode="bypass"`也不能取消hard floor或沙箱。

**平台后端不可用时默认拒绝执行。**`sandbox.mode`开着、平台后端缺失或所需隔离探测失败、`sandbox.unavailable_policy`是`refuse`时，`_invocation`抛`SandboxUnavailable`，`LocalBackend.run`把它变成失败的`RunResult`，文案给出原因和显式的不安全替代设置。`warn`恢复原来不受保护的执行行为，附一行日志。

粒度仍然是整个安装一个设置：不分agent、不分工具、不分命令。`wrap_command`接收显式策略，手上有策略的调用方可以传进去，但目前没有任何地方按调用点给出不同的策略。

### 3. 覆盖面

修复前审计确认了25个执行面：其中2个已经使用共用沙箱策略，另外23个没有统一分类。这个数字保留为历史基线。完整U01—U23台账位于[架构文档](sandbox-architecture.html#cover)，逐项记录来源类别、强制边界、保留原因和验收条件。

当前分类如下：

| 执行类别 | 当前边界 |
|---|---|
| 本地模型命令 | bash、process、本地`execute_code`、cron direct、memory写入器MCP shell和one-shot MCP启动使用`LocalBackend._invocation`；无人值守与写入器路径强制启用沙箱 |
| spawn agentic function | `SandboxPolicy`与authority字段显式序列化到子进程；缺少执行能力时拒绝宿主副作用 |
| 直接文件工具 | `write`、`edit`和`apply_patch`在修改文件前检查规范化后的可写根与不可配置保护路径 |
| cron prompt与direct job | 创建和管理需要schedule capability；签名不可变执行spec固化principal、scope、cwd、内容、policy与hash；触发时不能请求或获得更高权限 |
| 嵌套Claude Code | 禁用内置命令与文件副作用工具；受管MCP替代工具检查暂存工作区路径、记录审计，并强制shell命令使用OS沙箱 |
| 动态program导入 | 自动导入只接受owner登记来源；模型可写目录不能成为导入来源 |
| Docker与SSH后端 | 配置的容器或远端主机是执行边界；工具规则、authority、审批和审计继续生效，但不声明使用宿主原生沙箱 |
| 固定argv与owner管理员路径 | 确定性的Git/store操作、已配置plugin/MCP/hook、显式安装或升级命令保留各自声明的边界，不计为模型shell命令 |

`permission_mode="bypass"`不再先于安全检查。hard constraints与capability检查先执行；subagent保持非交互，scope不能超过caller，也不能申请升级。被忽略的provider参数`sandbox="read-only"`不计为保护；进程隔离由显式策略快照和上表边界提供。

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
| **OpenProgram** | 有 | Seatbelt和bubblewrap，仓库内 | 宿主，被包住 | 是（`sandbox.mode`为`workspace-write`） |

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
| **读** | 整盘，deny列表出厂为空 | 整盘，deny-read引擎出厂为空 | 容器内只看得到两个挂载；**宿主**侧read工具不受限 | 不受限 | `denyRead`预置`~/.ssh`、`~/.aws`、`~/.gnupg` | 不受限；文件工具有一份read-deny列表，代码自己注明"不是安全边界" | `*.env`走ask | 不受限 | 受沙箱本地命令可读宿主减去凭证deny清单；直接读取需要`fs.read` authority，但不属于OS沙箱进程 |
| **写** | 默认拒绝加allowlist，另有硬编码的dotfile和`.git` deny | 默认拒绝，`.git`/`.codex`/`.agents`受保护 | 容器内工作区挂载默认`:ro`，除非`workspaceAccess: "rw"` | 不受限 | `allowWrite: [".", "/tmp"]`，`denyWrite`含`.env`、`*.pem`、`*.key` | 对`/etc`、`/boot`、docker socket等敏感路径拒写 | 只有规则 | 不受限 | 受沙箱命令与直接文件工具都执行cwd/配置可写根和保护路径检查 |
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

最后一列的分布是最有用的部分。`openclaw`在Docker缺失时直接拒绝运行，并给出两条出路（`src/agents/sandbox/docker.ts:324-333`），镜像缺失时也拒绝拿一个通用镜像顶替。`claude-code`是照跑，但它自己的源码写清了为什么必须出声：修过的那个bug是`isSandboxingEnabled()`在依赖缺失时静默返回false，注释写的是"This is a security footgun — users configure allowedDomains expecting enforcement, get none."我们原来的行为正是那个bug描述的状态，现在`sandbox.unavailable_policy`默认是`refuse`。

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

## 第三部分：实施决策与记录

### 9. 缺口、先例、步骤

修复前基线中的每条缺口、第二部分提供参考的实现，以及§10中已经实施的步骤。

| 缺口（第一部分） | 谁解决了、怎么解决（第二部分） | 步骤 |
|---|---|---|
| macOS上`/dev/null`不可写 | `codex-cli` `seatbelt_base_policy.sbpl:18-21`；`claude-code`用`require-all`加`vnode-type CHARACTER-DEVICE` | 1，**已完成** |
| exec白名单误伤git、python、node | 两家都放开`process-exec`、依靠子进程继承沙箱；`openclaw`限制混淆而不是限制路径 | 1，**已完成** |
| Linux上tmpfs遮蔽`/tmp`下的工作目录 | 这是挂载顺序问题 | 1，**已完成** |
| 整盘可读，没有deny-read引擎 | `openclaw`的挂载源deny列表、`hermes-agent`的注册表推导环境变量剥离、`pi-mono`扩展的`denyRead`，八家中有三家预置清单 | 2，**已完成** |
| 环境变量全量继承 | `hermes-agent` `local.py:78-99`从provider注册表推导黑名单；`openclaw` `sanitize-env-vars.ts:1-19`按名字pattern加取值启发式匹配 | 2，**已完成，采用白名单** |
| Linux共享PID命名空间，宿主进程可读可杀 | `codex-cli`和`claude-code`都传`--unshare-pid`；`claude-code`还把`signal`和`process-info*`限定在`(target same-sandbox)` | 2，**已完成** |
| 开关在线程、spawn、嵌套CLI三个边界上丢失 | `codex-cli`每次exec重建argv；`openclaw`在每个调用点解析策略 | 3，**已完成** |
| 没有配置面 | 每个有沙箱的框架都有；`openclaw`从zod生成JSON schema | 3，**已完成** |
| 不可用时静默放行 | `openclaw`强制拒绝并给出处理提示，另有`doctor`提前预警；`claude-code`源码把静默版本称为security footgun | 3，**已完成** |
| 默认关且启用后不可用 | `codex-cli`默认启用`read-only` | 4，**已完成** |
| 沙箱和审批不联动 | `claude-code`在沙箱内免审批；`codex-cli`失败后移除沙箱重试；`openclaw`把容器作为边界并跳过allowlist | 5，**已完成，采用精确沙箱重试** |
| `permission_mode="bypass"`在risky工具检查之前短路 | `hermes-agent`把不可绕过规则放在所有bypass之前 | 5，**已完成** |
| 按前缀匹配的命令allowlist可被wrapper和Unicode绕过 | `openclaw`的argv解包器、`hermes-agent`的NFKC加ANSI归一化 | 5，**已完成** |
| cron worker触发时没有审批，subagent关闭审批 | `hermes-agent`的按上下文策略：cron默认deny，subagent自动deny并记录审计 | 5，**已完成** |
| 嵌套Claude Code CLI的文件工具不受`wrap_command`控制 | `openclaw`拦截嵌套agent的`command/`和`fs/`方法，注入受管替代工具 | memory写入器，**已完成** |
| 没有违规审计 | `claude-code`把内核deny行归因到具体命令并返回模型；`openclaw`在专用`agents/tool-policy` logger记录策略判定 | 与第2步一起，**已完成** |
| 没有CPU、内存或进程数配额 | `hermes-agent`的`--pids-limit 256`、限定大小的tmpfs、600秒前台上限；`openclaw`提供`pidsLimit`/`memory`/`cpus`/`ulimits` | **不在范围内** |
| 配置文件没有写保护 | `claude-code`显式deny所有settings.json；`codex-cli`保护`.codex`/`.git`/`.agents` | 与第2步一起，**受保护program/config根已完成**；Git hooks与仓库config为兼容性保持opt-in |
| 没有对沙箱设置执行静态检查 | `openclaw` `src/security/audit-extra.sync.ts`的具名检查项 | 独立配置安全工作，不属于沙箱运行时完成条件 |

### 10. 修复顺序

以下五步均已实施。这里保留原有顺序，用于记录依赖关系与验收条件。

**1. 可用性，已完成。**`process-exec`不再限制，子进程继承profile，所以`git`、`python3`、`make`、`clang`、conda python以及`/sbin`和`/usr/sbin`下的东西都能跑。`/dev/null`、`/dev/zero`、`/dev/random`、`/dev/urandom`、`/dev/tty`通过`require-all`加`vnode-type CHARACTER-DEVICE`可读可写，`2>/dev/null`正常。Linux上`--tmpfs /tmp`发在cwd bind之前，工作目录落在`/tmp`下也不会消失。macOS上仍然挡着的是`ps`和`top`，因为Seatbelt根本不允许把setuid二进制exec进沙箱。

**2. 凭证屏蔽，已完成。**deny-read清单出厂装弹（§1.3）。macOS上每条glob同时发`deny file-read*`和`deny file-write-unlink`，被禁读的路径不能用删除操作反推存在性；Linux上目录用`--perms 0000 --tmpfs`、文件用`--ro-bind /dev/null`屏蔽，宿主上不存在的路径跳过，因为只读的根让bubblewrap没地方创建挂载点。子进程环境变量用白名单而不是从provider注册表推导的黑名单：推导出来的名单要跟着注册表一起重建，白名单会自己丢掉没见过的名字，`openclaw`那条兜底名字pattern留作`sandbox.pass_env`的底线。Linux加上`--unshare-pid`，否则刚从子进程里去掉的key又能从`/proc/<agent_pid>/environ`读回来。deny-write覆盖agentics目录，这层保护按操作面分成两半。文件工具面（`write`、`edit`、`apply_patch`）上它无条件成立：`validate_write_path()`在解析任何策略之前就拒绝写入agentics目录和agentic源注册表，任何配置都够不到这道检查。命令面（`bash`、`execute_code`）上这层保护存在于沙箱策略里，因此在`workspace-write`和`read-only`下成立，在`sandbox.mode=danger-full-access`下不成立——该模式下shell面本就不设防，这正是这个模式的含义。git hook和git config是同一形状的逃逸，但保持opt-in，因为禁掉`.git/hooks/**`会让`git init`和`git clone`失败，而在第5步之前没有升级路径。

**3. 开关语义，已完成。**`ContextVar`已经删掉。策略在包装命令的那一刻从配置里的`sandbox.*`解析，asyncio任务到线程、spawn子进程、嵌套CLI三个边界都扛得住，因为文件不属于任何上下文。它同时坐在权限层之下，所以`permission_mode="bypass"`短路掉的是审批卡而不是沙箱。`wrap_command`接收显式策略供手上有策略的调用方使用；目前还没有按工具或按调用点给出不同策略的地方，那正是"调用点可覆盖"原本要买到的东西。平台工具不可用时默认拒绝执行，并给出两条出路。

**4. 默认开，已完成。**新安装使用`workspace-write`；已有配置中显式的`danger-full-access`保持不变。

**5. 审批联动，已完成，分为三个独立决策。**

*正向*：只读工具、显式allow规则和安全编辑路径继续免审批。不能仅因bash位于沙箱内就统一免审批，因为`workspace-write`仍可修改或删除仓库文件。

*反向*：结构化沙箱拒绝可以申请一次本地owner精确批准。重试使用放宽后的OS沙箱策略，不直接在宿主执行；agentics hard floor和凭证过滤继续生效。

*向下*：`_hard_constraint_violation`和capability检查位于规则、审批与bypass之前。cron和subagent不能建立交互审批路径。命令匹配会移除ANSI和NUL、执行NFKC、解析透明`env`包装，并拒绝持久化复杂shell表达式。

权限规则和`SandboxPolicy`保持为两个输入：前者表达owner同意，后者表达进程实际可访问的资源。

配套profile修补清单已经完成：工作目录插入profile前先转义，信号和进程信息只允许同沙箱目标，Linux传入上述命名空间与capability参数，两条路径统一使用`/bin/bash`，macOS收窄sysctl访问、删除通用`mach-lookup`，临时写入只允许当前`TMPDIR`。`--unshare-user`仍不加入：非setuid bubblewrap会自行创建用户命名空间，setuid构建不接受该参数。

CPU、内存和进程数配额明确不属于本沙箱项目。Linux PID namespace隐藏宿主进程，但不限制进程数量。通用配置静态检查属于独立配置安全工作；provider忽略的`sandbox="read-only"`参数已标明不提供强制能力，也不计入运行时边界。

### 接memory写入器

memory写入器有两个执行面，`wrap_command`只够得着其中一个。

MCP`shell`工具（`memory/management/tools.py` → `workspace.shell()`）在OpenProgram进程内执行。它以暂存目录为cwd调用`LocalBackend._invocation(..., force_sandbox=True)`，因此即使interactive bash被显式关闭，只要宿主原生沙箱不可用，该工具仍会拒绝执行。

Claude Code CLI子进程（`memory/agent_runtime/claude_code.py`）是另一回事。它的`Read`/`Write`/`Edit`/`Grep`/`Glob`在CLI进程内部执行，`wrap_command`碰不到，而`permission_mode="dontAsk"`把它自己的审批也关了。这个CLI进程也不该被包进沙箱：它要调Anthropic API，而沙箱没有网络。

CLI进程仍不进入OS沙箱，因为它需要访问Anthropic API。它自带的`Read`、`Write`、`Edit`、`Grep`、`Glob`和`Bash`现在被显式禁用。OpenProgram通过MCP提供五个文件操作的替代工具：路径必须位于暂存工作区内，`sources/`下的写入会被拒绝，每次结果都记入写入器审计。MCP `shell`替代工具调用`LocalBackend._invocation(..., force_sandbox=True)`；平台沙箱不可用时直接拒绝执行。

当前边界是：**嵌套CLI可以访问模型API，但不能调用自带的文件或命令工具；暴露给它的文件和命令操作全部由宿主MCP执行，命令强制经过OS沙箱。**

它挡住的威胁是具体的，不是假想的。只要挂了消息渠道，进入写入器prompt的文本就是攻击方可影响的：入站消息正文里带着发信人自己在平台上设的显示名。从那里被执行的命令本可以读`~/.openprogram/auth/*/default.json`并写进某个topic文件，而记忆库内容会回到之后会话的上下文里，这是一条不碰网络的外传路径，也正是"网络已经断了"没能覆盖的部分。

### 11. 拒绝的可读性与协商

执法层已经完整；本节设计的是它上面那一层。动因是实测事件而非假想：一个agent在`permission_mode="bypass"`下撞上`**/.env`的deny-read规则，收到一句裸的`Operation not permitted`，随后花了数轮把key搬进`secrets/glee.env`——绕行能成功，因为deny列表匹配的是文件名而不是内容。边界守住了，但结果是绕开owner协商出来的，而不是和owner协商出来的。栅栏另一侧也有同样形状的失败：Claude Code社区记载了要"五轮"配置才能让一条`.env`拒读在它两个互不协调的层上都生效，官方文档也警告宽泛的`allowRead`会悄悄把deny想保护的东西重新暴露。共同的教训是：一个不能解释自己的边界，要么被绕过（我们），要么被配错（他们）。我们的缺口在可读性，不在执法。

五个部分，按杠杆排序：

**具名拒绝。** 到达模型的沙箱拒绝作为tool result要点名命中的deny glob，并说明两条正路：申请升级（唤起owner卡片），或请owner修改`sandbox.deny_read`。文本明确排除第三条路：把受保护内容搬移或复制到glob匹配不到的路径。现在模型只能从平台错误文本反推`Operation not permitted`；改后拒绝本身就是一条可路由的指令。`sandbox.violation`事件已带结构——这里扩展的是面向模型的文本，不是事件。

**协商卡片，带可持久化的结果。** escalation审批渲染成专用卡片：被拦路径、命中规则、风险说明，三个选择。*本次放行*就是现有的精确升级重试。*总是允许此路径*是新增：把具体路径写进新配置键`sandbox.allow_read`，语义遵循Claude Code为`allowRead`/`denyRead`重叠所记载的"更窄路径获胜"规则——allow条目只在更宽的deny里重新打开点名的那条路径，同等精确度的deny仍然获胜。不可配置的hard floor（`~/.openprogram/auth/**`、agentics目录）完全排除在`sandbox.allow_read`解析之外，任何卡片点击都打不开它。*拒绝*维持原状。这补上了事件暴露的闭环：正路（点一下卡片）变得比绕路（数轮搬文件）便宜。

**状态可见。** 聊天顶栏的权限徽章增加沙箱指示，`bypass`的标签改成说清它的含义："跳过审批（沙箱仍生效）"。这个模式从未承诺解除OS边界，但屏幕上没有任何东西这么说过；事件里owner的困惑（"我开了bypass怎么还有沙箱"）有一半是文案bug。

**预设优先于键。** Security面板在一屏内呈现两层——权限规则与沙箱策略并排——配三个具名预设：*strict*（出厂默认）、*balanced*（`**/.env`移出deny-read，凭证与网络仍关闭）、*open*（`danger-full-access`，按它应有的警示样式渲染）。改预设让两层保持一致，owner不用学glob语义——这是对Claude Code用户抱怨的跨层配置负担的直接回应。

**prompt里的激励对齐。** 系统提示加一行：沙箱拦截读取时，申请升级或把拦截告知用户；绝不通过搬移或复制secrets来规避路径规则。执法层分不清"善意挪文件"和"外传前的暂存"，所以诚实路径必须是被指示的那条——而且在协商卡片之后，也是最短的那条。

本设计刻意不改的：双层架构（审批做决定，沙箱做约束）、deny先于bypass的判定顺序、hard floor、出厂即加载的默认值。不新增第二个执法点；上面每一部分都只是呈现、持久化或prompt。

---

## 实现状态

截至2026-08-10，修复顺序第1—5步和扩展架构第04—08步均已实现。新安装默认`workspace-write`；已有配置中显式的`danger-full-access`保持不变。

- hard constraint和固定权限档能力检查在权限规则、审批及`permission_mode="bypass"`之前判定。
- cron保存带签名且固化owner权限档的不可变执行spec，以强制沙箱和禁止审批升级的方式无人值守执行。`execute_code`、agentic子进程和one-shot MCP使用同一策略边界。
- `write`、`edit`和`apply_patch`执行写入根检查。自动导入只接受owner登记来源，并为已有官方clone提供校验后迁移。
- 已配对渠道发言是可信来源，并可追加source memory。未配对群组发言不进入agent，只归档为`pending`；pending证据仍可检索，hold队列准入与读取过滤推迟到第二批。只有本地interactive owner可以提升。
- 沙箱拒绝采用结构化结果。只有本地interactive owner可以批准一次精确重试；重试策略仍保留hard floor和凭证过滤。持久批准保存规范化后的精确操作，复杂shell只能单次批准。
- 嵌套Claude Code内置副作用工具已禁用，改用受管MCP文件与shell工具。

第11节（拒绝的可读性与协商）截至2026-08-22为已设计、未实现：拒绝尚未点名命中规则，`sandbox.allow_read`不存在，escalation卡片没有"总是允许此路径"选项，权限徽章不显示沙箱状态，也没有Security预设面板。

最终验证记录（2026-08-10）：本机完整受跟踪测试集（排除integration）为2731 passed、4 skipped、1 xfailed；GitHub Actions run 31398444213的Python 3.11、3.12、3.13、Web、文档和示例job全部通过，其中Linux Python 3.11为2723 passed、12 skipped、1 xfailed。该runner先启用Ubuntu 24.04的非特权user namespace能力，再执行真实cron bubblewrap用例，因此不会把“已安装但不能工作”的二进制计入Linux覆盖。macOS Seatbelt与Linux bubblewrap真实矩阵覆盖git、Python、npm、make、conda、凭证拒读、工作区外拒写和网络拒绝。

已知限制：

- Windows没有宿主原生沙箱后端。启用沙箱时默认拒绝命令，OpenProgram不会自动选择Docker。owner必须显式设置`sandbox.mode=danger-full-access`，或显式设置不安全的`sandbox.unavailable_policy=warn`。
- macOS拒绝在该Seatbelt profile中执行setuid二进制，因此`ps`和`top`不可用。
- Linux不能表达`**/.env`这类中段通配deny-read；已知具体路径会被遮蔽，该glob只在macOS生效。Linux敏感内容要使用精确路径，或`/absolute/path/to/secrets/**`这类具有确定前缀的目录级规则。
- 沙箱策略以安装为粒度，不支持per-tool沙箱覆盖；authority与权限规则继续提供逐操作控制。
- 为保证`git init`与`git clone`正常，工作区内git hooks和仓库config默认可写；owner可将其加入`sandbox.deny_write`。
- CPU、内存和进程数配额不在范围内。当前timeout和Linux namespace控制不能表述为资源配额。
