# Windows 支持

Windows 支持按可独立交付的层级推进。原生 CLI/server、浏览器 UI、release
installer 和带签名的 Desktop 发行是相互独立的层。Windows 沙箱作为可选 WSL2
委托层提供，不让原生 CLI 依赖 WSL。

## 支持层级

| 层级 | 契约 |
|---|---|
| W0 | 接受社区修复、记录已知缺口，并让不支持的入口返回明确错误；不承诺 Windows 支持。 |
| W1 | CLI 和 server 原生运行于 Windows，可使用完整 Ink 终端 UI 与浏览器 UI，MCP token 管理可用，Windows CI 契约通过。缺少 raw input 的终端回退到 Rich。沙箱执行和 Desktop 不在此层级内。 |
| W2 | 支持 Windows release archive、PowerShell release installer 和 Windows 专项 `doctor` 检查。Doctor 检查长路径配置，并给出 Defender 排除建议，但不修改 Defender 设置。 |
| W3 | 发布带签名和自动更新的 Electron Desktop。SmartScreen 使未签名构建不适合作为普通用户 release channel，因此不把未签名构建视为受支持发布。 |
| W4 | 本地命令隔离委托给已安装 WSL2 发行版中的 bubblewrap。`auto` 能力检测保证缺少该可选后端时原生 Windows 仍可使用。AppContainer 和 Job Objects 不会被表述成等价的文件系统与网络隔离。 |

未安装 Desktop 的机器继续以 W2 作为受支持的回退路径。W3 是当前 Desktop
发行目标。W4 为可选且可独立部署的层级。

## 工程规则

平台相关行为进入 `openprogram/_compat.py`，或由该接缝选择平台 adapter。产品
模块优先使用能力检测。不支持的功能在入口处报错，不在深层抛出迟到的
`NotImplementedError`。如果 POSIX 安全属性只能采用较弱替代方案，该决定必须
明确并记录。没有 Windows CI 覆盖的平台修复不算完成。

POSIX 的 `0600` 和 `0700` 不是 Windows 验收条件。Windows W1 使用用户 profile
原本继承的 ACL，不移除 ACL 继承，也不改写访问项。原子文件替换和跨进程文件锁
仍是功能要求，因为它们防止半写入和写入丢失；它们不是权限加固策略。

## W1 实现

源码开发 installer 创建隔离的 checkout `.venv`，安装 npm lockfile，构建浏览器与
Ink 终端界面，安装选定的 Python extras，并在用户 `PATH` 上提供稳定的
`openprogram.cmd` launcher。`-Minimal` 只安装 Python CLI/server 路径。

两种 CLI installer 同时提供原生 PowerShell launcher。生成的 PowerShell 脚本
使用带 BOM 的 UTF-8，兼容 Windows PowerShell 5；batch 脚本使用不带 BOM 的
UTF-8，并由 ASCII 前导语句选择代码页。Batch launcher 解析内嵌路径时关闭
delayed expansion、转义百分号，并在退出时恢复调用者的代码页且保留 Python
退出码。原生测试在 OEM、中文和 UTF-8 代码页下运行含 Unicode 与 shell 特殊字符
路径的生成脚本，并检查 PowerShell 参数传递。Installer 不修改执行策略或系统语言。

Ink 在所有操作系统上都按能力启动。Windows Terminal 和 ConPTY 能保留继承的 stdin
console handle，因此进入完整全屏 TUI；MinTTY 等无法进入 raw input 的终端会在 UI
边界失败，恢复 stdio 后再启动 Rich 回退。ConPTY 可能在一次 read 中同时交付可打印
文字与 Enter，因此 input tokenizer 会把控制字节和文字片段拆开；bracketed paste
仍作为一个原子输入事件处理。

MCP token 创建使用唯一临时文件与原子 hard-link 发布，因此并发创建者不会彼此覆盖。
读取时仍会复核已打开对象是普通文件，并重新验证目录祖先。Windows 保留 profile
继承的 ACL，不模拟 POSIX ownership 或 `0600` mode bits。

Windows 进程检查使用 PowerShell CIM，不再依赖 WMIC。持久 worker 使用当前用户、
最低权限的 Task Scheduler task。Checkpoint history、Undo/Reapply、review diff、
backup create 和事务式 restore 在 CPython 不提供 descriptor-relative directory
操作时选择路径回退。该回退拒绝 symlink 与 junction/reparse traversal，重新验证
父目录身份，并使用二进制与原子文件操作。

本地 shell 工具在已安装 Git Bash 时使用它，否则回退到 Windows 自带的
Windows PowerShell，不再让 `cmd.exe` 解析面向 Bash 的命令。后台 shell 与进程树
清理统一使用无窗口进程创建，避免 Desktop 中的 agent 运行闪出控制台窗口。工具
契约把该入口描述为 host shell，并要求可移植的文件操作优先使用专用 file/search
工具或 Python，而不是假设机器上存在 Unix coreutils。
Shell 源码传递集中在 `_compat`：Git Bash 通过临时环境变量接收原样源码，执行前
移除该变量，避免 MSYS 参数解析折叠内嵌反斜杠。PowerShell 使用 UTF-16 编码源码，
输出 UTF-8。Python 子进程 I/O 默认使用 UTF-8，除非调用者已显式配置。
两种传递方式都保留命令退出码和现有进程树超时、取消约定。

Program discovery 对 catalog 中的应用直接查询 distribution metadata，不会为每个
Program、每条 WebSocket 连接重建 Python 的完整 import-to-distribution 映射。完整
Windows runtime 中该全量文件系统扫描代价尤其高，会让每次硬刷新后的会话与页面
数据明显延迟，因此不进入连接热路径。

Session store 关闭时取消待执行的索引计时器，在索引锁之外等待进行中的写入，
再刷新最新 registry 快照。旧调用者复用已关闭的 store 时，后续写入改为同步，
不重新启动后台计时器。失败的写入保留 dirty 状态与进程退出重试。这样可以避免
Windows 退出流程遗留写入线程和文件句柄，同时保留存储一致性检查。
如果线程资源不足导致索引计时器无法启动，会移除未启动的句柄并同步保存待写快照。
线程创建恢复后，后续更新可重新使用后台刷新。

Push 与 pull request 检查只保留同一 workflow、同一分支或 PR 的最新运行，避免
过时提交持续占用 Windows 原生 runner。手动安装验收与定时 smoke 使用独立运行组，
不会被后续 push 取消。各 Windows matrix 继续覆盖两种原生架构。

Windows CI 分为两部分：

- core job 覆盖兼容接缝、checkpoint history、backup/restore、升级行为、installer
  契约和 Task Scheduler adapter；
- installation smoke job 执行完整 PowerShell 安装，检查隔离环境与 Web build，启动
  worker，运行 `doctor`，然后停止 worker。

进程存活与创建时间标识使用 Windows 原生只读查询，不使用信号零探测。
文件操作日志使用共享跨进程锁适配器。正向应用与失败回滚使用不同的备份
文件名，避免 Windows 的重命名规则阻断自动恢复。

项目文件查询通过兼容层选择目录接口。工作区预览批量读取 Git 树、索引和
不可变对象，并限制输出大小；回归测试约束 Git 调用次数，避免随变更文件数
线性启动进程。自更新日志读取沿用 Windows 继承 ACL 的约定。这只证明状态
展示和恢复记录可移植，不代表独立的 macOS 控制器与安装流程已支持 Windows；
后者仍需 Windows 实现及原生端到端验收。

回滚后的诊断与隔离源码修复使用同一套可移植状态检查。模型提供的 LF 编辑
可以匹配统一使用 CRLF 的源码，并保留原文件换行格式；重复匹配仍然拒绝，
混合换行文件要求精确匹配。候选测试的原生执行与激活仍属于上述控制器缺口。

## W2 实现

正式 Release matrix 在原生 Windows runner 上构建完整 Windows x86_64 与 arm64
product runtime，发布确定性的 `OpenProgram-<version>-runtime-windows-<arch>.zip`
及其 SHA-256 文件。ZIP 只有一个 `runtime/` 根目录，包含受控 CPython、平台对应的 Node.js
executable、独立 Ink bundle、预构建 Web 与文档资源、providers、channels、Programs、
Playwright Chromium 和模型数据。Runtime verifier 会实际执行 Ink 启动探针，成功后
才记录 `tui.ink` capability。

公开 `install.ps1` bootstrap 解析 stable release，并从该不可变 tag 下载 PowerShell
installer。Installer 解压前验证 checksum 和每个 ZIP entry，拒绝 link 与 reparse
point，在暂存目录中验证 runtime capability manifest，并在隔离状态中完成 worker
cold-start。每个 runtime 的独占文件锁串行化验证、发布与激活。候选全部通过后才
移入不可变版本目录，再原子替换 active PowerShell launcher。Release 保留在版本化
目录中，上一份 launcher 也会保留；候选验证失败既不发布版本，也不改变 active
launcher。复用已缓存版本时仍会重新验证。Installer 全程不修改 ACL。

ZIP 解压前先验证所有条目名称，再通过 Windows 扩展长度路径流式写入，避免
PowerShell 5 旧版目录解压 API 的限制。复制时检查实际展开字节数与条目长度，
保留空文件和显式目录；检查与失败候选清理也使用扩展路径 API，避免深层依赖目录
留下无法清理的半成品 runtime。这些操作不修改系统长路径注册表设置。

激活前会准备两个 launcher 文件并保存原内容，再逐个原子替换。后续替换失败时，
按逆序恢复已经发布的入口。恢复失败时，在报告的恢复路径保留原件，并返回明确错误。
不需要 backup 参数的 .NET 文件替换使用 PowerShell `NullString`，避免 Windows
PowerShell 5 把 `$null` 绑定成非法空路径。

Managed upgrade 通过兼容接缝选择 Windows ZIP 和 tag 下的 PowerShell installer。
`doctor` 将长路径注册表状态和 Defender 实时扫描/排除状态作为非阻断提示。这些查询
均为只读，不会启用长路径或修改 Defender。

## W3 实现

Desktop runtime 准备在 checkout 构建目录内的独立暂存目录中完成构建或安装，
使用独占锁串行发布。新产物准备完成且版本与 Desktop 包一致后才替换旧产物。
发布失败会恢复旧产物；若恢复也失败，则保留备份并明确报告恢复路径。
清理失败不会破坏已发布的 runtime，会同时报告保留的暂存路径和具体错误，
并继续释放发布锁。
清理通过 Windows 扩展长度路径处理深层和只读构建文件；删除目录链接时不访问
链接目标。
此流程只准备打包输入，不会替换已安装 App。PowerShell 构建辅助脚本在各自构建步骤
结束时恢复调用者的 workspace、工具链、Python 下载目录与浏览器缓存环境变量，
失败路径也遵守同一约定。
共享的 `verify-release-version.py --installed-app` 前置校验接受 Windows 安装目录
或其中的 `OpenProgram.exe`。它不启动外壳，而是读取 PE 产品版本，再要求其与
runtime manifest 及内置隔离 Python 读取的包版本一致。Windows 版本末尾的第四段
零会被规范化，非零修订号不会被静默丢弃。此只读校验本身不实现 App 刷新。

Tag workflow 构建 Windows x86_64 与 arm64 Electron 应用，以及按用户安装、可选择目录的
NSIS installer。封装前通过正式 PowerShell release installer 暂存同一份完整 W2 runtime，
随后从 `win-unpacked` 验证内置 runtime、worker health、Web chat 路由和不可变
Program 边界。`OpenProgram.exe` 与 installer 都必须具有有效 Authenticode 签名；
缺少签名凭证或签名无效会直接阻断 release job。未签名 Windows build 仅用于本地
开发验收，不是可发布渠道。

封装后的 Desktop 使用内置 managed Python 启动 worker。原生 Terminal 在 Windows
选择 Windows PowerShell，必要时回退到 `COMSPEC`，并让 `node-pty` 使用 ConPTY。
浏览器资料导入从 Windows 常规安装位置和 Local AppData 发现 Chrome、Edge、Brave
和 Chromium。Windows Desktop 状态沿用用户现有 ACL；封装、导入与更新路径均不
改写 ACL。

Desktop updater 按当前 x64 或 arm64 架构选择精确的
`OpenProgram-<version>-win-<arch>.exe` release asset。
它依次验证 release metadata、字节数和 SHA-256，再要求 Authenticode 有效，才打开
installer；任一验证失败都会删除候选文件。安装仍是用户可见并确认的交接，不会在
后台静默替换运行中的应用。

Windows x64 和 arm64 CI 安装包括原生 PTY 模块在内的 Desktop 与 Web 依赖，
运行 Desktop 契约与 Web 单测。Web 测试夹具显式选择浏览器语言，并在依赖源码文本
的断言中统一 checkout 换行格式。Desktop 契约
覆盖范围包括 ConPTY 命令选择、浏览器导入、跨窗口 tab 事务、封装 worker 启动、
release 选择、checksum 失败和签名失败。

## W4 实现

Windows 命令沙箱委托给默认 WSL2 发行版，并通过 bubblewrap 运行命令。兼容接缝
发现真实的 WSL2 发行版，检查 Bash 与 bubblewrap，探测 namespace 能否创建，并用
该发行版的 `wslpath` 转换 Windows 路径。随后 bubblewrap 提供与 Linux 主体一致的
边界：只读根目录、显式可写 workspace roots、读写拒绝路径、隔离的 PID/IPC/UTS
namespace、私有临时目录，以及在有效策略未允许网络时使用的 network namespace。

`sandbox.mode` 默认值为 `auto`。在 macOS 与 Linux 上，它会在本机后端可用时启用；
在 Windows 上，只有 WSL2 委托能力探测通过后才启用。缺少可选后端时，命令继续以
原生未沙箱方式运行，避免可选功能导致整个 CLI 无法使用。用户显式选择
`workspace-write` 时仍采用 fail-closed，并在执行入口明确报告缺少 WSL2 或
bubblewrap。能力探测和沙箱准备都不会修改 Windows ACL、owner、文件 mode、
Defender 或 WSL 配置。

没有选择 AppContainer，是因为让它访问有用的现有 workspace 会引入应用特定的
capability 和 ACL 工作。Job Objects 适合未来加入 CPU、内存和进程数配额，但无法
提供当前沙箱合同要求的文件系统与网络边界。因此，原生 AppContainer 隔离和资源
配额属于独立后续工作，而不是 W4 的未完成部分。

## 实现状态

| 层级 | 状态 |
|---|---|
| W0 | 已作为基础兼容契约实现。 |
| W1 | 实现已落地并完成本地验证；仓库 Windows jobs 是合并门禁。 |
| W2 | 已实现；Windows Release 构建与 installer jobs 是发布门禁。 |
| W3 | 已实现并完成本地验证；配置签名凭证并通过 Windows Desktop release job 后才可发布。 |
| W4 | 已通过可选 WSL2 与 bubblewrap 委托实现；原生 AppContainer 隔离和资源配额留作后续工作。 |
