# 升级

升级行为取决于安装类型。stable 安装只在已发布版本之间变更，不跟随 `origin/main`。

0.7.0 是从 v0.6.6 进入 updater release 线的一次性过渡：Desktop 用户手动安装 v0.7.0 DMG；CLI/server 用户重新执行一次完整 release installer：

```bash
curl -fsSL https://openprogram.io/install | sh
```

安装 v0.7.0 后，后续 stable release 可使用下面的 Desktop 设置和 `openprogram upgrade` 命令。

## 桌面 release

Desktop 会自动检查最新 stable GitHub Release，也可以在“设置 → General → Application → 立即检查”手动检查。

- macOS：有新版本时选择“下载并打开 DMG”。OpenProgram 会选择与架构匹配的完整 `unsigned` DMG，下载到用户指定位置，验证字节数与 SHA-256 后打开。退出 OpenProgram 并替换 `OpenProgram.app`；macOS 可能再次要求通过“隐私与安全性 → 仍要打开”授权。
- Linux：从目标不可变 tag 重新执行 release installer。当前不发布 Linux 桌面包。

应用外壳与完整 product runtime 一起替换；`~/.openprogram` 下的状态保持不变。

## CLI 和服务器 release

检查或升级到最新 stable release：

```bash
openprogram upgrade --check
openprogram upgrade
```

需要指定不可变 release 时使用：

```bash
curl -fsSL https://openprogram.io/install | OPENPROGRAM_VERSION=X.Y.Z sh
```

Windows 对应命令为：

```powershell
$env:OPENPROGRAM_VERSION = "X.Y.Z"
irm https://openprogram.io/install.ps1 | iex
```

命令从不可变 release tag 取得版本化 installer。installer 下载同平台 runtime archive，在暂存目录验证 checksum 和完整 capability manifest，并在发布或激活该版本前执行 worker cold-start。macOS/Linux 会串行化升级并原子切换 `current` symlink；Windows 原子替换 PowerShell launcher，并保留上一份 launcher。激活前失败时，旧版本仍保持选中，尚未发布的暂存 runtime 会被删除。版本目录会保留，因此回滚时用上一版 `OPENPROGRAM_VERSION` 重跑同一命令即可；运行中的 worker 不会自动重启。

升级后重启登录服务：

```bash
openprogram worker restart
```

## 恢复对话内自更新

对话内打包离线执行。依赖基线必须与候选的 `uv.lock` 和
`scripts/release/product-runtime.json` 完全一致；控制器使用保存 runtime 中固定版本的
构建工具，以及既有 npm/uv/Electron-builder/node-gyp 缓存的私有副本。Electron 平台归档
必须匹配可信控制器固定的版本、架构和发布 SHA-256，并作为 candidate 只读的
`electronDist` 输入。候选的 Web、文档、wheel 和
Desktop archive 仍重新构建。缓存缺失或依赖基线不匹配时，在激活前停止更新；
不会因此允许联网下载或沿用不匹配依赖。该构建过程及真实已安装验收仍是发布条件。

packaged worker smoke 只使用控制器选择的单个私有 loopback 端口，且不会使用默认端口
18100。浏览器自动化由重新校验过的保存 controller runtime 单独检查；该 runtime 位于
candidate 所有可写路径之外，并要求候选浏览器资源树 hash 与其一致。控制器不会执行
可写的构建 runtime 副本。两项检查都完成后才允许激活。
固定的 macOS 图标渲染检查也由可信控制器执行，不向候选构建开放宿主图形服务。

源码 checkout 的聊天工具 `self_update_prepare` 接受可选的 `verification_plan`
参数。计划包含在强制 owner 审批中，并与不可变请求一起保存。例如：

```json
{"schema":1,"checks":[{"id":"diagnostics","assertion_id":"acceptance-1","entry":"/api/diagnostics","timeout_seconds":10,"max_output_bytes":65536}]}
```

每条 assertion（`acceptance-1`、`acceptance-2` 等）必须恰好对应一个 check，共 1–32 条。
示例中的所有字段均必填，`schema` 必须是整数 `1`。
check ID 唯一，最多 64 个字符，以字母或数字开头，只允许字母、数字、下划线和连字符。
每项必须指定 1–60 秒的整数超时和 1–262144 字节的整数输出上限（`ui:main` 为 1–1572864）。
目前支持 `/api/commands`、`/api/diagnostics`、`/api/doctor`、`/healthz`、`/chat`、
`cli:version`、`cli:help`、`test:python` 和 `ui:main`。任意 URL、查询参数及不支持的字段都会在
创建更新前被拒绝；只有 `test:python` 还必须提供下述 `argv`。

重启后的 verifier 收到相同计划，调用 `self_update_observe(check_id="diagnostics")`，
不能另传 `entry` 或执行参数。执行层应用已批准的限制和原总期限；签名证据必须匹配
对应 check 和 assertion，不能复用到另一验收项。省略计划时保留原有的 HTTP-only
验收行为，不增加权限。

提供计划的更新中，验收、回退后诊断和源码修复 Job 都收到相同的已批准计划与
iteration policy，以及本次尝试的超时。修复后的子候选保留原目标、assertion、
check ID、限制、模型和权限，不能重新延长迭代总期限。诊断和源码修复仍只有各自的
读取工具；在 prompt 中包含验收计划不代表授权它们执行检查。

固定 CLI 检查使用已安装 App 的 Python 执行 `-I -B -m openprogram`，分别追加
`--version` 或 `--help`，不接受模型提供的命令，也不通过 PATH 查找解释器。
缺少原生沙箱、兼容的 runtime manifest 或匹配的包/App 构建 revision 标记时，
准备阶段拒绝 CLI 计划。执行使用独立临时目录，禁止网络访问和 App/源码写入；
除必需的 runtime 与临时执行路径外，禁止读取 owner 的 HOME。
原生验收只允许单进程：沙箱禁止创建进程，包括普通或分离的子进程、`fork` 和
`posix_spawn`。需要子进程的检查不能通过这个适配层验收。这项限制适用于这些 CLI
检查和下述候选检查，不改变独立的源码修正 required-test 或构建执行路径。
证据绑定 runtime 身份、完整调用参数和退出状态；非零退出、超时、取消、身份变化、
输出超限或清理失败均不能通过。这两个入口只检查 CLI 启动/帮助，不证明任意功能行为。

候选源码测试可以使用这样的检查项：

```json
{"id":"source-test","assertion_id":"acceptance-1","entry":"test:python","argv":["tests/verify_feature.py","expected"],"timeout_seconds":30,"max_output_bytes":65536}
```

`argv` 包含 1–32 个字符串，每项最多 4096 字符，不能包含 NUL。第一项是相对于候选
根目录、已提交的普通 `.py` 文件，不能是符号链接、绝对路径、父目录跳转或解释器
选项。脚本路径以 ASCII 字母、数字或下划线开头，其余只允许字母、数字、下划线、
斜杠、点和连字符，总长最多 511 字符。后续项作为原样脚本参数，更新前批准后，
verifier 不能修改。使用已安装候选版本的 Python，在登记的 candidate worktree
根目录执行 `-I -B SCRIPT ARGS`。隔离模式不会自动把候选根目录加入 Python 导入
路径；测试需要导入候选代码时必须显式设置。已登记的候选目录是额外允许读取的位置，
即使它位于 owner 的 HOME 下；其他 HOME 数据仍不可读取。候选源码保持只读，临时测试数据应写入
独立 `TMPDIR` 或 `HOME`，不会自动安装依赖。原生 CLI 的前置条件和限制同样适用。
源码缺失、变脏、未登记或脚本变化都会阻止验收；证据记录源码 revision、脚本摘要和
调用参数。`candidate_test` 结果只证明源码测试执行，不证明已安装 App 行为。

对原会话的 App 主窗口进行只读截图，可以使用：

```json
{"id":"main-capture","assertion_id":"acceptance-1","entry":"ui:main","timeout_seconds":30,"max_output_bytes":1048576}
```

准备阶段要求兼容的已打包 UI 验收描述文件、runtime 身份，以及恰好一个已连接的
Desktop 主窗口。安装前，候选包和回退包都必须具有匹配的截图、后端和前端能力绑定；
缺少该能力的旧包不能使用这个计划。verifier 收到 PNG 图片和 accessibility tree，
不是只有文件路径；批准的输出上限覆盖包含 base64 图片数据的整个截图 JSON。
PNG 必须为非交错的 8-bit RGB/RGBA，每个维度最多 16384，总像素最多 3200 万。
解析后的 verifier 模型必须声明支持图片输入。准备阶段拒绝纯文本模型；重启恢复在
创建 verifier Job 前再次检查该能力。如果图片能力已不可用，启动过程记录错误，
不会改用纯文本 verifier 执行验收。
已排队的 Job 也在执行入口再次检查，检查发生在调用模型之前。

截图绑定活动验收 Job、候选 revision、worker、原会话路由和确切的主窗口连接。
过期、取消、重放或身份不匹配的请求不能产生通过证据。用户输入、导航、窗口变化、
截图资源冲突、输出超限或清理不完整也会阻止截图成功。不授权任意 URL、JavaScript、
目标窗口、点击、导航或数据修改。截图期间，当前 Desktop 适配层拒绝该主窗口发起的
新页面网络请求、原生 IPC 操作和外部链接导航。该限制在证据上传前释放，失败时也会
释放，不影响其他窗口。它不撤销已经执行中的请求。原生适配层为该主窗口的 HTTP
请求添加验收标记；后端拒绝带标记的请求，
包括认证 bootstrap 和过期标记。检查期间，该窗口已有的 WebSocket 只允许观察回执
和取消确切的 verifier Job，以及下述确切批准的测试对象操作，不接受普通应用命令。

要在已批准的滚动之后截图，可添加 `interaction`：

```json
{"id":"scroll-history","assertion_id":"acceptance-1","entry":"ui:main","timeout_seconds":30,"max_output_bytes":1048576,"interaction":{"kind":"scroll","delta_y":-400}}
```

`delta_y` 为 -1200 到 1200 CSS 像素之间的非零整数，目标始终是原会话的主聊天区域，
不接受 selector 或脚本。候选包和回退包都必须具有 UI protocol 2，绑定实际原生滚动
适配层、后端限制和编译后的 UI 位置记录限制。Protocol 1 包仍只能截图，在安装前
拒绝滚动计划。截图和 accessibility tree 对应滚动后的状态；签名证据还记录滚动前、
滚动后及恢复后的位置数据。成功前恢复原位置，不保存临时滚动位置。截图失败时，
只在原目标和原期限仍有效时恢复自己的滚动。用户操作中断检查时，不恢复覆盖用户的
新位置。目标变化或恢复失败均不能通过。在滚动边界，位置可能不变；仅凭这一点不能
证明要求发生位移的断言。

要检查原会话的上下文图，可冻结视图检查：

```json
{"id":"context-view","assertion_id":"acceptance-1","entry":"ui:main","timeout_seconds":30,"max_output_bytes":1048576,"interaction":{"kind":"view","target":"dag"}}
```

`target` 只能为 `session` 或 `dag`，不能是 URL 或其他会话。检查从可见的原会话开始，
触发实际的视图切换控件，捕获指定视图，并在成功前恢复会话视图和滚动位置。开始时
已经选中 DAG、控件不存在或被替换、用户中断、恢复失败均不能通过。`session` 目标
不会切换视图，不能证明切换发生。临时视图不持久化，后台标签更新也不能保存它。
用户中断时，适配层不会强制恢复原视图或滚动位置而覆盖用户的选择。恢复失败时，
界面可能停留在请求的视图；检查报告失败，不宣称清理成功。
候选包和回退包都要求 UI protocol 3，额外绑定编译后的视图支持。Protocol 1 和 2
保留原有截图、滚动能力，但不能接受视图检查。

要验证实际重命名对话框而不修改真实会话名称，可批准临时测试对象、修改和清理动作：

```json
{"id":"rename-control","assertion_id":"acceptance-1","entry":"ui:main","timeout_seconds":30,"max_output_bytes":1048576,"interaction":{"kind":"test_object","object_id":"rename-fixture","action":"rename","initial_title":"Before verification","title":"Approved rename","cleanup":"restore-and-remove"}}
```

`object_id` 为 1–64 个字母、数字、`_` 或 `-`，以字母或数字开头。它只标识本检查的
隔离对象，不是已有会话或路径。初始名称和新名称必须不同，都是非空、两端无空白、
不含控制字符且最多 120 个字符的字符串。对象绑定 update、attempt、check 和一次性
操作 nonce，只存在于活动后端检查的内存中，不保存为用户数据。

适配层在原主会话中打开实际重命名对话框，输入确切批准的文字并按 Save。后端只接受
绑定 socket 和活动 Job 发出的确切对象、值和操作。收到确认后保存截图和可访问性树，
再按 Cancel 请求批准的恢复。成功要求后端状态依次为 initial、renamed、restored，
原名称恢复且自有对话框关闭。检查结束后删除临时对象；取消、超时或 worker 退出也会
使其消失。中断不能生成成功回执；清理只关闭本次自有测试对话框，不重命名真实会话，
也不恢复覆盖用户输入。

候选包和回退包都要求 UI protocol 4，绑定原生适配层、后端 handler、测试对象桥接及
编译后的实际重命名控件。旧协议不获得新权限。这项检查只证明对话框针对临时后端对象
的有限操作，不证明真实会话名称的持久化，也不批准其他数据修改。对象缺失、输入不符、
控件不可用、Job 失效、后端确认失败或清理不完整均为 inconclusive。

截图只能支持对所捕获状态的断言；没有观察到的
交互行为仍应判为 inconclusive。HTTP 响应（包括 `/chat` HTML）不能证明 App 界面
实际行为。其他副作用操作不受支持；完整验收能力和实际已安装 App 验收仍待完成。

这项源码 checkout 能力与 stable release 升级分开。macOS 上，对话内自更新使默认
App 保持维护状态时，可以在本地终端检查，不需要启动 Agent：

```bash
openprogram self-update status --json
openprogram self-update status UPDATE_ID --json
openprogram self-update repair UPDATE_ID
```

将 `UPDATE_ID` 替换为 `status` 返回的 ID。恢复要求交互式终端，并准确输入与操作、
版本和计划摘要一起显示的确认内容。没有 `--yes` 或强制解除维护选项。已授权的操作
只能在原来的十分钟期限内恢复执行；失败或过期后需要重新确认。

恢复使用更新前保存的控制程序。仍可回退时恢复旧 App；已经开始不可逆提交时，
只有原始验收通过证据完整才能完成提交。已取消且尚未激活的事务保持旧 App。
证据缺失或变化时继续保持维护状态。恢复会重启默认 worker，检查 App 身份和实际
服务，然后解除维护。它不会新建验收 Job，也不会改变原更新结论。独立的恢复结果
记录恢复了哪个版本；服务恢复不代表失败的功能已达到原始目标。

新的对话更新请求同时冻结一个只读诊断阶段。确认旧版本恢复后，worker 在原会话中
创建一次 **Post-rollback diagnosis** Job，使用已批准的模型和 profile 读取失败证据，
报告原因与修正建议。该任务不能修改源码、执行 shell、安装软件或授权下一次更新，
也不覆盖原验收和回退结论。

诊断期限最多为回退后的五分钟；原迭代授权期限更早时取更早值。重启不会重新计时，
已终止的 Job 不重跑。通过普通 Job 取消入口停止诊断；`self_update_cancel` 仍只取消
激活前的更新。新更新会取代待处理诊断。模型不可用或证据无效只终止诊断，不重新
限制已经恢复的服务。`self-update status --json` 在有结果时包含 `diagnosis_result`。
缺少冻结诊断配置的旧请求保持原有行为。诊断报告本身不修改或安装代码。

新请求独立冻结 **Post-rollback source repair** 阶段。首次授权包含隔离修正与所列
`required_tests`。默认模式在再次安装前另行审批；显式批准的 `bounded_auto` 同时允许
在原限制内再次安装。implementation/test 诊断可触发一个只读模型 Job
提出文本编辑，由控制器逐项校验、新建 linked worktree 和 commit，并在无网络、无
App 写权限的原生沙箱运行冻结测试。原 worktree 不变。默认只修改原 changed_paths；
`bounded_auto` 使用原授权路径模式。受保护的 runtime、审批、安装器、依赖和 Git
文件不会自动修改。无效路径或 old_text 无法唯一匹配会停止修正。

修正和测试共用回退后的十分钟期限，原授权期限更早时取更早值。新更新、取消或
超时停止本阶段及测试进程。模型运行时可使用普通 Job 取消；模型结束后，在原会话
调用 `self_update_repair_cancel(update_id)` 取消仍运行的测试。重启不重放部分编辑、
提交或测试。失败 worktree 和证据保留供检查。缺少冻结修正配置的旧请求不获得此能力。

`self_update_status` 和 `self-update status --json` 包含 `source_repair_result`。
聊天工具与需要 owner 认证的 Web 历史接口
（`GET /api/self-updates?session_id=…`）和详情接口
（`GET /api/self-updates/{update_id}?session_id=…`）使用同一份只读投影。
历史包含已结束的 attempt，支持有上限的 `limit` 和 `cursor` 分页。
`candidate_revision` 与 `target_app` 表示请求的安装目标；
`last_verified_runtime` 包含最近一次匹配验证的 SHA、PID、时间和来源，
没有对应证据时为 null，不表示 worker 仍在线。`state_revision` 是状态计数器，不是 Git revision。
可使用投影中 verifier 的 `evidence_id` 或某条 assertion 的 `evidence_refs` 值调用
`GET /api/self-updates/{update_id}/evidence?session_id=…&evidence_id=…`。
此接口要求 owner 认证和原会话，只返回已校验签名结果引用的观察记录，不支持读取任意文件，
不返回凭据或配置。保存的 HTTP/HTML 响应文本不能证明 App 窗口已实际渲染；证据被修改或
校验失败时返回错误。

查询不初始化或修复更新状态，损坏状态返回错误，不显示为空历史。
Running 无法读取更新快照时返回 `self_update_error`。
投影不包含凭据、原始日志和配置；修正摘要包含状态及已生成的新 candidate SHA。
这不替代 CLI 独立的恢复检查输出。

会话按更新序列和尝试次数显示持久化的自更新历史；“加载更早更新”读取下一页。
Running 使用同一状态卡。目标版本与最近已验证运行版本分开显示：没有运行证据时
保持“未知”，先前验证不代表当前仍然在线。展开“更新详情与证据”可查看 assertions，
并以纯文本加载需要鉴权的证据。状态无法读取时保留最近快照，明确标注可能过时和
最近同步时间，然后自动重试。

“请求更新操作”中的取消、停止迭代和重试按钮只向原会话的未发送草稿追加请求，
保留原有草稿内容。重试要求输入新候选提交的完整 SHA。需要在原会话发送草稿，
才会请求工具操作。这些按钮本身不安装、不取消，也不批准操作；正常工具鉴权和
强制审批仍然适用。状态卡只有在控制器返回新状态后才改变。

`candidate_ready` 表示新 commit 与全部已配置测试通过校验；未配置必需测试时为
`awaiting_tests`。测试缺失、失败或源码漂移记录 `failed`；取消与超时分别为
`cancelled`、`expired`。这些状态均不代表已安装。

对已测试的修正版本，在原 owner 会话调用 `self_update_retry(update_id, candidate_sha)`。
即使使用 bypass，也必须进行单次审批，显示精确 SHA、变更路径、测试和剩余预算。
批准返回后重新检查 Git 内容和测试日志；任一内容变化都会拒绝提交。
`awaiting_tests` 候选不能通过这个入口批准，需要 owner 发起明确测试的新请求。

新的 `bounded_auto` 请求必须包含未来的总 `deadline`、允许路径和非空 `required_tests`。
只有独立冻结了迭代授权的请求才可自动提交后继版本；旧请求仅有 mode 字段不会获得权限。
每个 child 保留原目标、assertions、源码基线、model/profile 和 policy。
首次更新计为 attempt=1，最多三次包含首次。预留 child 就消耗一次预算，提交失败也不退还。
重启复用原 child 和期限，不重置预算。

`self_update_iteration_cancel(update_id)` 停止整个序列，包括待批准操作、诊断、源码修正和
候选测试。已进入激活或验收的 child 必须完成事务或安全回退，但不能再创建后续 attempt。
其他普通 Job 不受影响。

聊天工具 `self_update_status` 的 `iteration` 字段提供 root、parent、attempt 限制、总期限和
提交状态。`submitted` 只表示请求已交给外部 supervisor，不代表安装成功。
child 仍须完成打包、系统检查、新验证 Job，以及 commit 或 rollback。
真实已安装 App 的验收是独立发布条件；fixture 测试不能证明当前 App 已更新。

worker 还会把有限的更新结果持久化到原会话，即使窗口已关闭，或者更新在创建验证 Job
之前就停止。worker 再次检查待投递结果时，仍使用相同的 update、attempt 和结果类型标识，不启动新的模型轮次，
也不移动会话 HEAD。写入中断后会重试；原会话或发起操作的 assistant 节点不存在时，
结果保持待投递，不重建会话，也不改投其他会话。结果持久化不会重新打开 App，
也不能证明安装成功；安装结论仍以更新阶段和已验证的运行证据为准。

Desktop 已具备接收本次更新专用受信任会话恢复请求的能力。它通过 owner 认证只解析
原会话，并在主窗口实际加载会话记录后确认。请求过期、会话删除或认证失败时仍可普通
启动，并显示恢复原因。用户切换页面后停止自动定位；可关闭的提示在原会话身份有效时
保留打开原会话的链接。恢复与加载确认都不会创建新的验证 Job，也不证明更新成功。
控制器现在会在激活前持久化恢复意图，并把不透明更新 ID 绑定到安装事务。
App 原本打开时，激活与回退都使用该 ID 重新打开；原本关闭时保持关闭。
普通 App 启动不会消费这些恢复请求。

候选与已安装 App 都必须包含匹配的打包恢复协议声明。声明缺失、不兼容或内容变化、
事务更新 ID 不匹配、冻结的 owner 配置无效，都会在替换旧 App 前阻止激活。
等待结束及恢复已准备的更新后，控制器会重新检查这些输入。打包和本地刷新脚本根据
实际 Desktop archive、installer、runtime manifest、后端及编译后的 Web 文件生成声明。
没有该声明的旧 App 需要先进行显式完整更新，才能使用对话恢复。
源码连接已经过 fixture 测试；真实已安装 App 的重启及会话/标签恢复验收仍待完成，
因此这还不是已可发布的完整端到端功能。

App 或普通 CLI 无法启动时，使用本次更新保存的独立入口：

```bash
"$HOME/.openprogram/self-updates/UPDATE_ID/recover.sh" status
"$HOME/.openprogram/self-updates/UPDATE_ID/recover.sh" repair
```

脚本使用 App 外保存的原 runtime，不传参数时默认只查状态。`repair` 仍要求 owner
在交互式终端确认，不绕过证据校验或过期授权。`recover.sh resume` 在原授权和期限内
调用原 supervisor，不批准新更新，也不重新创建验收 Job。

激活前，OpenProgram 还在本用户的 `~/Library/LaunchAgents/` 中发布
`ai.openprogram.self-update.recovery.UPDATE_ID.plist`。它在之后每次用户登录时运行一次，
不依赖 App；没有常驻进程或周期重试，写入文件也不会立即启动另一个控制进程。
恢复不在用户登录或磁盘解锁之前执行。当前登录会话中 App 和控制进程都停止时，
显式运行保存的脚本。更新结束后仅清理内容仍一致的本次登录文件；保存的 runtime、
脚本和证据继续保留。可信恢复文件缺失或损坏时需人工处理，不从未验证的 App 重建。

## 开发 checkout

本地已安装的 macOS App 在提交改动后，从当前 checkout 运行 `scripts/refresh-local-app.sh`。脚本重新构建 wheel 与 Desktop 资源，更新 PATH 和 App 内置的 Python 安装，并同步内置 Node 与 Ink 资源。完整内置运行环境通过实际检查后，脚本才重新生成能力清单并重启默认 worker。新增必需能力时，这个流程也会更新旧 App 的运行环境清单；检查失败会终止刷新，不会将未验证的能力标记为已验证。

复制后的 Node 可执行文件必须能够脱离原安装目录运行。脚本会在修改 App 之前检查这一点。如果 PATH 中的 Node 依赖相邻动态库，运行刷新脚本时将 `OPENPROGRAM_NODE_BIN` 设为独立 Node 可执行文件的路径。

在 source checkout 中，`openprogram upgrade` 执行开发升级流程，而不是 release installer。它验证 Git 目标，仅在相关源文件变化时更新依赖与构建产物，probe 新 checkout，并且只在 probe 成功后重启 worker：

```bash
openprogram upgrade --check
openprogram upgrade --dry-run
openprogram upgrade
```

历史 `openprogram update` 命令作为 `openprogram upgrade` 的兼容别名保留。

source checkout 的恢复细节见[服务器升级](../server/upgrading.zh.md)。
持续维护的架构、信任边界、界面状态和实现证据见
[正式版本自动更新](../reference/design/distribution/automatic-updates.html)。
