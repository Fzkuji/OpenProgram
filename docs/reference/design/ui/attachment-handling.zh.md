# 附件处理

本文是 Web、Desktop 和 Channel 入站附件的设计与实施方案。[英文版](attachment-handling.md)为准，文档侧栏的配套审阅页面展示决策和阶段。目标行为不代表已实现，状态集中列在文末。

## 当前实现与已确认问题

Web 输入框将图片读取为 base64，单独生成最长边 192 像素的缩略图，允许最多 5 MiB 的图片；发给模型的图片没有经过尺寸压缩。普通文件的前端上限为 32 MiB。后端 `_persist_attachments` 已经保存包括图片在内的上传文件，添加路径标记和文字／PDF 预览；文档不进入 `TurnRequest.attachments`，图片仍携带完整 base64。

`normalize_agent_turn_payload` 限制整个序列化执行输入为 256 KiB。最小图片请求中，200 KiB 的二进制内容经过 base64 编码后已经超过限制。本地复现没有调用模型，只验证执行输入大小，不验证图片解码。实际空白聊天日志也记录 `input_too_large`，但日志不足以确定原请求中哪个附件或字段导致超限。

执行准入发生在 `_append_msg` 之前，失败时会话及标题可能已经存在，但没有用户消息。`sendChatMessage` 返回的是 WebSocket 发出成功，`useChatSubmit` 随即清空文字和附件；未关联的命令错误仅显示短暂的通用提示，没有可恢复的提交记录。

后端单文件 32 MiB、每轮 64 MiB 的检查在 base64 解码之后；总量计数只增加新写入文件，去重命中未计入。保存阶段跳过的超限图片也没有明确从独立的模型附件列表移除。这些是源码确认的问题，未进行线上攻击测试。OpenAI Responses 转换在模型不支持图片时还会移除图片块，前端缺少对应的交付说明。现有标记解析、原始路径来源、预览路径、去重和文件访问策略继续保留。

源码：[上传与保存](https://github.com/Fzkuji/OpenProgram/blob/main/apps/server/openprogram_server/_webui/ws_actions/chat.py)、[执行准入与激活](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/agent/production_driver.py)、[输入框提交](https://github.com/Fzkuji/OpenProgram/blob/main/apps/web/components/chat/composer/submit/use-chat-submit.ts)、[图片读取](https://github.com/Fzkuji/OpenProgram/blob/main/apps/web/components/chat/composer/attach/image-attach.ts)、[错误处理](https://github.com/Fzkuji/OpenProgram/blob/main/apps/web/lib/net/action-error.ts)、[Provider 转换](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/providers/_shared/openai_responses.py)。

## 官方参考与采用范围

| 框架／接口范围 | 已核实设计 | OpenProgram 的选择 |
|---|---|---|
| Codex 公开 app-server 与 Rust 协议 | 输入区分 text、image 和 localImage；本地图片在请求序列化时转换。 | 分开附件身份和模型编码；不据此推断私有桌面实现。 |
| Codex attachment-store | 保存接口返回 URL 和可选 file ID，也存在 InlineAttachmentStore。 | 采用持久引用；不能声称 Codex 所有附件都不内联，也无需照搬多个存储后端接口。 |
| OpenCode V2 | prompt 准入前读取并验证附件，单项解码上限 20 MiB；图片尺寸与编码大小分别限制。该输入接口明确支持文字及 PNG/JPEG/GIF/WebP，PDF 等二进制不保证进入模型。 | 采用准入前校验和独立图片预算；保留我们的 PDF 分页能力。 |
| OpenClaw Gateway／媒体理解 | 支持受管理的媒体引用，区分原生视觉、已处理、跳过、失败；提取的文档文字标记为不可信；工具回退取决于运行环境能否读取文件。 | 采用明确的处理结果及运行环境可访问性检查；不把 Channel 暂存期限用于已提交历史。 |
| Claude Code 公开工作流 | 支持图片粘贴、拖入、路径及文件／目录引用。 | 保留这些输入方式；该文档不足以证明其完整存储架构。 |

来源：[Codex app-server](https://learn.chatgpt.com/docs/app-server)、[输入类型](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/user_input.rs)、[附件存储](https://github.com/openai/codex/blob/main/codex-rs/attachment-store/src/lib.rs)、[OpenCode V2 附件](https://opencode.ai/v2/docs/attachments)、[图片配置](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/config/attachments.ts)、[OpenClaw 媒体理解](https://docs.openclaw.ai/nodes/media-understanding)、[受管理图片](https://github.com/openclaw/openclaw/blob/main/src/gateway/managed-image-attachments.ts)、[云端附件部署](https://docs.openclaw.ai/gateway/cloud-sessions)、[Claude Code 工作流](https://code.claude.com/docs/en/common-workflows)。

这些资料涉及不同版本和接口。接口接受图片不等于持久保存，UI 显示文件也不等于模型已收到文件内容。

## 方案选择

| 方案 | 判断 |
|---|---|
| 直接增大执行输入限制以容纳 base64 | 不作为主要修复：重复序列化和庞大执行／历史输入仍存在，也不解决草稿丢失和 Provider 上限。 |
| 把所有图片压到约 192 KiB 以下 | 不采用：元数据还要占空间，细小文字截图可能不可读；压缩应服从图像和 Provider 预算，不是执行输入大小。 |
| 只保存 Desktop 原始路径 | 不适用于附件快照：后续编辑、临时文件删除和远程部署会改变读取结果；实时项目引用保留独立语义。 |
| 会话所属的不可变字节＋小型引用 | 采用：用一个本地存储修复共享准入边界，并统一回放、预览和传输使用的身份。 |

## 目标契约

附件在执行准入前完成保存和校验。准入记录及历史只持有有序、属于指定会话的小型引用；Provider 转换时才读取选定表示。界面记录实际交付方式，而不是把上传完成当作模型已读取。

引用包含 schema 版本、不透明附件 ID、所属会话、摘要、解码字节数、检测出的 MIME、文件名和可选的原始路径来源。客户端不能指定可信存储路径或创建所有权；文件名、路径属于数据，不是指令或访问凭证。

采用一个本地会话附件存储。原件不可变，必要时生成图片派生版本；原件放在所属会话目录中、Agent 可修改的 workdir 之外。保留现有 workdir 副本，或在文件工具读取时生成副本。绝对路径及 preview-path 标记继续作为兼容的显示／工具表示，不再承担不可变身份。暂不引入跨会话去重或远程对象存储。

现有 ExecutionStateBlobStore 需要 execution／attempt 所有权，不能直接承担执行创建前的上传，以及跨 attempt 的附件生命周期。复用摘要和完整性校验约定，不放宽它的所有权检查。

Web 使用鉴权的流式上传接口，完整校验后返回引用；接收字节前，通过服务端状态绑定临时聊天所属用户。Desktop 原始路径仅保留来源，排队和回放使用保存的快照。Channel、CLI 和旧版 inline 客户端统一经过同一入站处理。兼容解码必须先限制分配大小，新准入记录不再保存 base64。

### 原件、副本和公共读取边界

引用 ID 和摘要确定权威快照。投影记录将 `(所属会话, 附件 ID, 摘要)` 映射到 workdir 副本。向按引用读取的工具提供路径前校验副本；摘要一致则复用，否则从原件创建不覆盖既有文件的新副本并更新映射。保留 Agent 修改的文件作为普通项目文件，不能覆盖或当作原件。普通按路径工具仍读取所指路径的当前内容。

拟新增的鉴权接口 `GET /api/session/{sid}/attachments/{attachment_id}/content` 为附件预览和回放读取所属原件或明确指定的派生版本。`/api/file-raw` 继续服务实时文件及旧标记，不承担新快照身份。该接口不扩大任意文件读取权限；可能执行主动内容的格式只能下载或在隔离预览中打开，不能在应用 origin 下执行。

### 公共上传与提交契约

以下接口为设计，尚未实现。`PUT /api/session/{sid}/attachments/{upload_id}` 流式接收单个文件，声明文件名、长度、MIME、摘要并按实际内容验证。临时聊天在接收前原子登记鉴权调用者的上传归属，已有聊天仍需通过正常授权。同 ID 同内容的已完成上传返回原引用；不同内容冲突；未完成上传在旧写入者释放后可重新开始。

每个完成的上传还返回持久 `draft_claim_id`，按鉴权用户、会话和上传选项归属。服务端保存该 claim 及引用，把临时草稿归属作为 GC 的保留依据，不只作为浏览器提示。同一上传的重试复用 claim；再次选择相同内容使用独立 claim，即使原件字节已去重。用户明确移除附件或丢弃草稿时，鉴权接口 `DELETE /api/session/{sid}/attachment-drafts/{claim_id}` 只释放对应选项。关闭浏览器／断线不释放。接收成功仅把本次所选 claim 转为历史／execution 归属；拒绝时仍保留；其他选项及草稿编辑不受影响。存储额度可以拒绝新上传，不能静默淘汰仍被草稿引用的原件。

现有 WebSocket `chat` 增加 `submission_id`、有序附件引用及本次实际选中的草稿 claim ID。拟新增 `GET /api/session/{sid}/submissions/{submission_id}` 查询持久结果，必须先完成鉴权和正常会话读取授权。ID 本身不授予权限；无权访问和不存在的会话返回相同的安全未找到结果。已授权会话内不存在的提交才返回 `not_found`。execution 数据库中的 `chat_submissions` 以 `(session_id, submission_id)` 为键，保存调用者、规范化输入摘要、有序引用、状态、execution ID、确定的用户消息 ID 和安全错误码。准入前认领该键；同 `(session_id, submission_id)` 同规范化输入的并发请求共享结果和 execution。不同提交 ID 表示独立用户提交，即使内容相同也服从既有并发／排队规则，不能按内容自动去重。重载和重试沿用原 ID。

| 提交结果 | 含义与客户端行为 |
|---|---|
| `not_found` | 尚无持久认领记录；保留草稿，用同 ID 同内容重发，不创建新 ID。 |
| `pending` | 已认领但用户消息尚未确认保存；保留草稿并核对，服务端在崩溃后恢复或结束原操作。 |
| `accepted` | execution 和确定的用户消息都已保存；`chat_ack` 与查询返回同一提交 ID、消息 ID 和 execution ID，只有该结果清除精确草稿。 |
| `rejected` | 已明确失败；返回有界错误码并保留输入。修正后的草稿使用新提交 ID，旧 ID 重复请求返回原拒绝。 |
| `conflict` | 同键不同内容或引用顺序；不修改原记录，不创建新 execution。 |

查询、重载、重复发送均不能通过创建第二条 execution 结束 pending。服务端使用既有 execution／消息身份补全消息持久化或记录拒绝，标题不作为成功证据。

## 大小预算与交付

初始建议保持文档单文件 32 MiB、每轮解码后 64 MiB，并增加每轮最多 16 个附件。媒体内容移出后，执行输入仍限制 256 KiB，用于文字、元数据、权限规则及引用。这些是待验收的产品默认值，不是 Provider 的共同保证。

| 预算 | 含义与检查位置 |
|---|---|
| 上传 | 按实际解码字节计量；每个选中附件都计入，即使内容去重命中；准入前检查数量及总量。 |
| 旧 base64 | 解码前估算大小、严格校验编码，再核对实际大小；WebSocket 接收还要有独立帧限制，文件限制不能替代它。 |
| 图片表示 | 保留原件；建议派生图片最长边 2000 像素、base64 编码后最多 5 MiB，并继续服从 Provider 更严格的限制；校验内容 MIME 和解码结果；派生版本接收前限制像素／帧数、解码内存与处理时间，记录转换。 |
| 文字／PDF 预览 | 每项最多 4096 UTF-8 字节、每轮最多 32 KiB，包含边界标记和截断提示；另限提取时间和页数工作量。当前 PDF 按字符截断不能证明字节上限。 |
| 模型请求 | 转换时检查模型模态、图片数、尺寸、编码大小及 token／上下文预算；不能用元数据限制替代。 |

上传额度预留按 `(鉴权用户, 会话, upload_id)` 归属，在写入者活动期间占用暂存额度。中断、校验失败、取消或到期释放预留并删除未完成临时文件。完成上传后按实际存储计量直到回收，同 ID 重试不重复计存储。每轮 64 MiB／16 项则在认领提交时对有序选中引用重新计量，包括重复／去重内容，不与上传存储计量混用。同一提交不重复预留；并发 Tab 使用各自 ID，仍服从既有会话执行并发规则。已完成但未认领上传须核对服务端草稿 claim、pending 提交和历史归属后才能回收，断线本身不表示可删除。

动图、不支持的格式、透明通道和细小文字不能因转换而静默丢失重要信息。保留原件，说明采用的派生表示，在支持的情况下提供原始分辨率读取方式。解码失败属于附件失败，不因缩略图回退而声称成功。

| 输入 | 模型实际收到 | 界面状态 |
|---|---|---|
| 支持的图片＋视觉模型 | 已校验的原件或派生图片的原生输入 | 图片已包含；可查看转换详情 |
| 图片＋纯文字模型 | 仅当存在已启用、有权限且能读该文件的图像工具时，提供文件引用及工具读取说明 | 文件可供工具读取；图片未包含在模型输入中 |
| 文字／代码 | 有边界的不可信预览＋可访问引用 | 已包含预览；其余按需读取 |
| PDF | 有界文字预览／页摘要＋PDF 工具访问 | 已包含预览，或无可提取文字但文件可访问 |
| 其他二进制 | 文件引用，不伪造提取结果 | 文件可供工具读取 |
| 图片＋纯文字模型＋没有当前可用且授权的图像工具 | 以 `attachment_delivery_unavailable` 拒绝，不能静默移除图片 | 保留草稿，可切换视觉模型、启用授权工具或移除图片 |
| 缺失、无权限、损坏、超限 | 阻止该次提交，保留草稿 | 明确原因，可移除、替换或重试 |

原生 PDF 作为后续扩展，需要 Provider 能力、页数／字节／token 预算及集成验收。扫描 PDF 转图片、OCR、音视频和远程 URL 下载均不作为修复图片发送的前置任务。现有 `@`／直接输入路径继续表示实时项目文件，与明确附加的快照可区分。

工具禁用、策略拒绝或无法读取时，不构成有效回退。仍需批准时，保留输入，先完成既有审批流程再接收该交付方式。交付方案在准入及激活时各检查一次；接收后授权被撤回，应以持续具体错误结束该 execution，不能继续只回答文字。模型能力变化也不能静默丢图。

成本保证仅针对有界文字预览，不能声称全部模型成本 O(1)。视觉及原生文档 token 随内容表示变化，历史预览可能继续留在上下文中。十个 30 MiB 文件超过总量，应在准入前拒绝，不能作为成功上传示例。

## 提交、恢复与所有权

1. 在现有按聊天隔离的草稿／IndexedDB 中保存未发送文字、附件 ID、原始输入和稳定的客户端提交 ID。进度和错误始终归属原聊天，分屏同样适用。
2. 上传、校验、准备表示。上传完成不表示执行已接收。
3. 提交文字、有序引用和提交 ID。服务端核对所有权、大小、模态及元数据预算，记录提交与 execution 的映射和用户消息保存结果。成功确认必须指明已保存的用户消息及 execution。
4. 只清除被确认的提交快照；保留等待期间新增的文字和附件，不提前撤销失败／待确认附件的预览。
5. 明确拒绝时保留草稿和持续错误提示。断线／超时时显示结果未知，以同一 ID 查询和重试；不能因丢失 ACK 而创建另一条 execution。同 ID、不同内容属于冲突。

SQLite execution 与 Git 会话存储不是同一事务；执行准入到用户消息保存之间的崩溃需要显式恢复。保存失败不能返回成功，也不能留下看似运行中的空聊天。生成标题不算接收成功。

摘要校验确保 workdir 修改不能改变已排队输入。解析引用时验证会话权限；猜测 ID、跨会话引用、目录跳转、符号链接越界、过期授权必须拒绝。合法 fork／attach／export 保留或复制必要字节，并建立新所有权，不能只复制字符串。项目迁移依据当前会话位置索引解析。

| 生命周期操作 | 所有权规则 |
|---|---|
| 预览／回放 | 按不可变 ID 校验摘要和归属，读取选定原件／派生表示，不替换为可变副本。 |
| Fork／attach／merge | 通过既有会话授权后，将引用字节保存到目标会话并建立目标 ID，保留来源和顺序；失败则中止转移，不能产生失效引用；删除源会话不能影响目标副本。 |
| Archive／delete | Archive 保留；delete 在按既有删除策略处理活动工作后，仅删除本会话数据，不删共享项目目录。 |
| Export／import | 导出包含 manifest 和已验证原件，必要原件缺失时明确失败；导入验证摘要、创建目标所属 ID 后才展示历史。 |
| 项目迁移 | 通过既有位置机制移动完整会话所属存储并更新位置，提供中断恢复；引用不依赖绝对存储位置。 |

提取的外部文字统一添加来源边界，转义分隔符并限制总字节。来源标记不等于完整的 prompt-injection 防护；工具授权和文件访问检查继续独立执行。

只要服务端草稿 claim、历史、排队工作、checkpoint 或授权分支仍引用原件，就保留它。未认领上传仅在宽限期结束且完整核对引用后回收，宽限期在实现时配置。Archive 保留附件。删除、导入导出、迁移必须一并处理所属字节和 manifest；禁止通过删除项目 workdir 回收单个会话的附件。

## 实施计划与验收

每个阶段更新本文状态、产品文档和验证记录，再进入下一阶段。未通过该阶段端到端验收，不能称为可用。

| 阶段 | 修改 | 必须证明 |
|---|---|---|
| A：持久引用 | 统一入站与会话原件；准入 schema 兼容；激活／历史转换；Web 和 Channel 图片调用方 | 合法 1 MiB 图片保存后准确到达 mock Provider；新执行输入仅含引用且低于 256 KiB；重启、重试、损坏／缺失、迁移、越权均有测试；旧 inline 历史可读。 |
| B：可靠提交 | 流式上传、临时聊天归属、提交 ID、持续错误和精确 ACK 清理 | Web 入口覆盖超限、上传中断、丢 ACK、重载、重复提交、切换聊天、等待时编辑、上传额度释放、同 ID 并发重试、不同 ID 的独立提交及超过暂存宽限期的离线草稿恢复；不丢附件、不重复执行；默认 App 真实截图首次发送成功。 |
| C：交付与生命周期 | 有界图片派生、真实状态、模型切换、分支／保留／导出规则 | 纯文字和视觉请求、缺失／拒绝回退及接收后撤权、当前与历史图片顺序、Unicode 预览、含去重的总量、转换提示、分支／删除／迁移／导出及窄屏 UI 均有验收。 |
| 后续模态 | 原生 PDF、扫描文档、音视频 | 独立制定 Provider 契约和预算，通过验收才声明支持。 |

主要代码边界：`chat.py`、`production_driver.py`、`dispatcher/types.py`、`dispatcher/loop_runner.py`、会话位置和序列化、Channel 附件转换、输入框附件缓存／提交、命令错误处理及既有预览路由。不能只在 Web handler 修正大小，因为其他调用方共享执行准入限制。

验证使用真实 chat／intake 边界和可确定的 mock Provider，不只测试辅助函数。现有起点为 `tests/unit/attachments/`、`tests/unit/channels/test_channels_attachments.py`、`tests/component/agent/test_production_driver.py`，Web 包括 provisional-send 和 local-attachment-paths 检查。完整 Python／Web／Desktop 验证遵循仓库测试约定。实际实现完成还需独立规格与质量审查、干净提交，以及 `scripts/refresh-local-app.sh` 后的默认 App 验收；本方案不包含远程写入授权。

## 实现状态

已检查的既有能力：上传保存、会话内去重意图、原始／预览路径标记、有界预览意图、图片块、输入框 IndexedDB 草稿和文件预览。其限制及错误处理尚不满足目标契约。

已复现：最小图片执行输入中，100 KiB 二进制 base64 通过；200 KiB 与 1 MiB 被 256 KiB 限制拒绝。这不是完整上传或 Provider 测试。实际空白聊天日志独立记录了相同错误类别。

已设计、未实现：A、B、C 及后续模态扩展。本次文档更新不修改运行代码、已存聊天或安装的 App。[双向附件与预览文档](chat-attachments.html)负责显示及输出范围；本文负责入站存储、执行准入、内容交付和生命周期。
