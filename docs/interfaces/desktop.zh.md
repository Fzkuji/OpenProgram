# 桌面 App 与内置浏览器

设置是应用级独立页面。从头像菜单、应用菜单、快捷键或 Browser 菜单打开设置，不会改变任何标签页及其前进、后退历史。点击标签页即可返回该标签页的内容。设置分类仍支持通过 URL 直接打开；恢复已保存的标签页时会自动移除旧的设置历史记录。


本地 App 刷新会保留解包原生依赖的文件权限，并在此前刷新丢失执行权限时恢复 node-pty 终端辅助程序的执行权限。

每个标签独立保存后退和前进历史，默认入口页是该标签的初始页面。后退和前进只恢复当前标签中的页面，切换标签不增加历史。从另一个入口页打开相同目的地时，两个标签各自保留。后退后选择新目的地，只替换当前标签的前进记录。历史随标签保存；网页内部的 URL 导航由浏览器控件处理。

macOS 与 Windows Desktop App 将 OpenProgram 显示为多 Pane 工作区。每个 Pane 可以放 Files、聊天、内置 Browser 或 Terminal；Pane 可以分屏，也可以移到其他应用窗口，底层会话与浏览器 tab 不会因此改变。

关闭最后一个标签后，标签栏和中间内容区域都保持为空，不会自动创建标签或显示默认入口。点击顶部加号可打开新标签。

后退和前进按钮也包含初始新标签页。从初始页打开应用或内置页面后，可以后退回到初始页，再前进回到原页面，始终使用同一个标签。后退后选择其他页面会替换原来的前进记录。

macOS 从 [GitHub Releases](https://github.com/Fzkuji/OpenProgram/releases) 下载对应架构的 DMG，把 `OpenProgram.app` 复制到 `/Applications`；当前 macOS 发行版使用 Developer ID 签名和 Apple 公证。Windows 只安装已发布 release 附带的带签名 `win-x64.exe` 或 `win-arm64.exe`；某个版本没有带签名 Windows EXE 时，使用该版本的 CLI/server 与浏览器 UI。完整步骤见[安装](../install/install.zh.md)。

Terminal Pane 在 macOS 使用 login shell，在 Windows 通过 ConPTY 使用 Windows PowerShell。两个平台的封装 App 都从内置 managed Python 启动 worker，不依赖系统 Python 或 Node.js。

在 Linux 从源码运行 Desktop 时，终端优先使用已安装且为绝对路径的 `SHELL`，否则依次回退到 `/bin/bash`、`/bin/sh`，不要求安装 zsh。若没有可用 shell，终端会在启动进程前明确提示缺失项。

从会话标签打开其他会话时复用当前标签，即使其他标签已显示该会话也保持独立。从默认入口页打开会话也使用当前标签。从其他类型页面打开会话时，可以选择已有会话标签或新建标签。后退和前进始终只作用于当前标签。草稿输入和后台更新的标题在导航和重新加载后保留，已删除会话会从全部历史中移除。

## 打开 Browser

新建 Pane 后选择 **Browser**，或从应用 tab 栏新建浏览器 tab。Browser home 与已加载网页使用同一套浏览器控件：

- Back、Forward、Reload/Stop、Home、地址/搜索输入、收藏当前页、Bookmarks、外部打开和 Browser 菜单。
- 默认可见的书签栏；可以从 Browser 菜单或 Browser settings 隐藏。
- 响应式控件：Pane 变窄时，低频动作移入 Browser 菜单；地址输入、Back、Reload/Stop、收藏当前页和菜单仍然可用。

Browser 菜单只管理浏览器动作：新建浏览器 tab、Bookmarks、History、书签栏显示、profile 导入、清除浏览数据和 Browser settings。窗口与 Pane 操作仍归 OpenProgram 窗口菜单管理。

## 页面跳转、popup 与右键操作

网页决定一次操作是在当前页面跳转，还是请求新的浏览上下文。普通链接、表单提交和页内导航保留在当前 Browser tab；带 `target="_blank"` 的链接和调用 `window.open()` 的脚本会创建独立 Browser tab，并立即激活新 tab。

在网页内右键 HTTP(S) 链接，可以选择在新 Browser tab 中打开或复制链接地址。普通页面右键菜单提供 Back、Forward 和 Reload；可编辑字段会按网页报告的可用状态提供 Undo、Redo、Cut、Copy、Paste 和 Select All。菜单动作只作用于打开该菜单的精确 Browser tab。

## Bookmarks 与 History

书签栏直接显示导入或本地维护的 Bookmarks bar 内容。非空的 Other bookmarks 与 Mobile bookmarks 保持为独立文件夹入口。超出宽度的项目进入有限宽度的溢出菜单；嵌套文件夹逐级展开，并在当前窗口高度内滚动。

Bookmarks manager 提供文件夹树、当前目录列表、搜索、favicon 和条目菜单。History 按本地日期分组，每行只显示时间、favicon、标题和域名。Desktop Browser 数据与后端状态分开：History 与持久化 `webtabs` partition 位于 Electron 的当前用户应用数据目录，聊天、项目、Programs 和 worker 配置仍位于 `~/.openprogram/`。清除浏览数据不会删除这些后端状态。

## 导入已有浏览器资料

在 macOS 与 Windows 上，OpenProgram 可以识别本地 Google Chrome、Brave、Microsoft Edge 和 Chromium profile。导入始终由用户显式发起：分别选择来源浏览器、profile 和需要的数据类型。

| 数据 | 行为 |
|---|---|
| History | 在支持的数量上限内复制 HTTP/HTTPS 访问记录，并合并到 OpenProgram History |
| Bookmarks | 保留 bookmarks bar、other bookmarks、mobile bookmarks 与嵌套文件夹结构，同时过滤无效 URL 和重复项 |
| Cookies | 用临时来源浏览器进程解密可用 Cookie，经校验后通过 Electron Cookie API 写入；部分网站仍会要求重新登录 |

OpenProgram 不导入密码、支付或地址自动填充数据、下载记录、缓存、localStorage、Service Workers、浏览器扩展或扩展存储，也不修改来源 profile。

## Agent 访问分屏 Browser Pane

当一个聊天轮次所在应用窗口中存在可见的内置 Browser Pane 时，OpenProgram 会在模型首次回复前附加该精确 WebTab 的有限页面描述。Agent 会获得页面标题、origin、可见文本、ARIA landmarks 与浏览器控制工具。Browser Pane 在左侧、右侧或聊天上的画中画预览中都不影响访问，也不要求应用窗口或 Browser Pane 获得操作系统焦点。

若 Agent 在你停留在聊天时打开页面，桌面端会把该实时 WebTab 显示为角落小窗。小窗可以展开为「左聊天右网页」分屏、接管中间栏，或关闭预览且不销毁标签。关闭预览只是隐藏小窗，页面仍可从右侧栏的 **会话资源** 中访问。网页版（普通浏览器里的 UI，不是桌面 App）没有原生 BrowserView，同一预览降级为 iframe 或「在新标签打开」。

动作始终绑定发起聊天的窗口与 WebTab。默认使用 DOM、ARIA、页面文本和 element refs；只有视觉任务或结构化方式无法定位页面时，才使用一张当前 viewport screenshot。该路径不增加 OCR、object detector、多轮裁剪、component memory、vision memory 或 workflow replay。

每个预览都绑定实际会话，即使在同一个聊天标签内切换会话也不会改变归属。切换会话时隐藏原预览，并恢复当前会话自己的预览。仅保留在会话中的网页对其他 Agent 不可见、不可操作。点击 **打开页面** 将同一个网页显示为独立标签页，其他会话可以发现它，并在当前 Agent 释放排他控制后接管。打开标签页不会转移原会话的预览归属。

预览标题旁显示“自动预览”或“固定预览”。自动预览跟踪 Agent 正在操作的页面。固定预览保持当前页面；取消固定预览后恢复自动显示该页面。该选择不改变 Agent 的操作目标，也不会恢复或启动 Agent，也不改变页面生命周期或顶部标签。会话资源没有单独的固定控件。默认聊天预览为 300×198.75（300×168.75 画面加标题栏）。拖动标题栏移动小窗；从四边或四角调整大小。小窗保持画面比例（默认 16:9），没有可见的边角拖块，指针在外框变为调整大小光标。调整预览不会缩放或改写底层页面。资源面板每个分组标题是整行控件，右侧为与左侧边栏项目行相同的箭头。

## 浏览器扩展

Chrome Web Store 与 Edge Add-ons 页面可以作为普通网页打开，但 OpenProgram 不安装浏览器扩展，不下载 CRX，不从其他浏览器导入扩展，也不提供扩展管理页。应用使用标准 Electron/Chromium。Electron 只暴露部分 Chrome Extensions API，并明确不以兼容任意 Chrome Web Store 扩展为目标；OpenProgram 不维护定制 Chromium/Electron 分支，也不为扩展兼容增加另一套浏览器运行时。完整 runtime 内已有的 Playwright Chromium 只属于浏览器自动化后端，不承载 Desktop Browser Pane 或扩展。

需要扩展 OpenProgram 本身时，使用 [OpenProgram Plugins](../capabilities/plugins.zh.md)、Skills、MCP servers、Programs 或 agent tools。这些能力不修改内嵌网页运行时。

持续维护的工程规范见[内置浏览器设计](../reference/design/ui/built-in-browser.html)与 [Web Use / Computer Use 边界](../reference/design/integrations/web-use.html)。

## 会话资源

点击右侧栏的 **资源**，打开 **会话资源**。通过侧栏折叠按钮收起或展开面板。面板仅显示当前会话所属的资源，没有搜索框。切换会话会更新所列资源。面板列出完整的软件或环境对象：网页、VM／桌面关联，以及集成明确登记的容器或远程环境。代码执行、命令、脚本、后台进程和输出属于 Activity；文件视图保持独立；持久 Terminal 环境关联会话后显示在同一资源列表中。没有归属记录的视图和新建草稿会话不显示会话资源；打开已有归属的资源时保留其会话上下文。

有会话归属的 Agent 网页默认不占顶部标签，固定或显式分屏时保留在顶部；没有归属记录的旧网页保留在顶部。点击网页可打开原有视图；网页支持固定和关闭。折叠面板或分组不会停止资源。选择资源后，资源面板继续显示在视图右侧。关闭网页后，活动列表立即移除该项，并处理关闭请求；如果未确认关闭，该项会重新显示。

Docker 与 SSH 命令保留在代码执行／Activity 流程中，执行命令不等于创建软件资源。GUI Harness 记录配置的桌面或 VM 关联；其他集成必须登记实际软件或环境对象及其身份，不能用命令、镜像名或进程代替。点击环境资源可查看目标和状态。

其他集成可以在可信运行时会话内使用 `openprogram.session_resources.resource_use(kind, title, target)` 上报完整的软件或环境对象。上下文自动记录真实会话，退出后释放使用记录。系统不会从任意 shell 命令文本推断资源；资源 URL 会去掉凭据、查询参数与片段。

## 重启后继续运行中的会话

worker 在配置的自动继续期限内重启后（默认两小时），原来正在运行的会话会使用正常执行时已经记录的输入和结果，在原会话中自动继续，不需要单独保存，也不增加重启提示。手动暂停、取消和已完成的任务保持停止。已经确认完成的工具操作不会重复执行；外部操作的结果仍不明确时，需要先核对结果。界面重新连接时保留已有聊天内容。


会话显示正常消息和工具结果，包括你要求执行的软件更新结果。聊天中不另设软件更新历史、更新操作按钮或更新恢复提示，打开会话也不会启动软件更新历史轮询。

## 重启与保留的浏览器页面

Desktop App 或 worker 进程重启后，你尚未关闭的网页会在后台自动恢复。应用保留同一保留 tab、会话与分支分组，以及最后确认的地址和标题。它在该保留 tab 后创建新的活页面；上一进程的句柄不再有效。不会为同一保留 tab 再开一份副本，不会重新打开你已关闭的网页，也不会显示你已隐藏的预览。紧凑画中画、纵向 Files / Activity / Resources 侧栏以及分组资源行保持原样。文案跟随 App 语言设置。

网页正在回来时，资源行留在原分支，显示 **正在恢复网页…**。恢复失败时同一行显示 **网页恢复失败**。新的活页面之后需要重新连接时，显示 **需要重新连接**。这些行不会进入通用的不可用分组。明确关闭的网页和已终止的终端显示在 **已关闭的资源** 分组中。恢复网页不会启动或继续 Agent。空闲页面仍可直接操作。新任务仍按常规重新观察页面并检查权限。恢复会重新加载最后确认的 URL，并使用 Desktop `webtabs` 分区里已有的站点存储；上一进程中未保存的 DOM 与表单不会恢复。没有保留 tab 描述的旧资源记录不会自动重建，该行留在原分组并显示 **网页恢复失败**。这一有界重启行为已在默认 App 中实现。

每个预览都绑定实际会话，即使在同一个聊天标签内切换会话也不会改变归属。切换会话时隐藏原预览，并恢复当前会话自己的预览。仅保留在会话中的网页对其他 Agent 不可见、不可操作。点击 **打开页面** 将同一个网页显示为独立标签页，其他会话可以发现它，并在当前 Agent 释放排他控制后接管。打开标签页不会转移原会话的预览归属。



## 网页操作与暂停

你可以随时点击、滚动、输入、切换或关闭内置网页，这些操作不会暂停 Agent。Agent 也可以操作 OpenProgram 自己的网页。只有聊天输入框旁的任务暂停按钮控制执行。

关闭任务所需的网页后，Agent 重新获取页面；页面确实已关闭时按最后已知地址重新打开，并读取当前状态后继续。旧点击或提交不会自动重放。登录状态及未保存内容无法恢复时，Agent 会说明具体情况。

会话历史先加载最近一页。向上或向下滚动时，都会自动读取附近的消息，保持当前阅读位置和正在输出的新内容，无需点击加载按钮；请求失败后会自动重试。远处的历史页会从内存缓存移除，返回附近时重新读取。重新打开会话时，如果原来阅读的消息仍在当前分支中，会恢复到该消息的位置。“跳到最新”会直接读取最近一页。DAG 数据在打开对应视图时加载，普通 Chat 不计算绘图坐标。Markdown 渲染缓存设有容量上限，避免浏览过的长内容持续累积，不改变已保存的历史或模型上下文。

主会话和分屏各自提供“跳到最新”：只要已加载窗口后还有新消息，按钮就会保留。跳转失败后可以重试。在消息区域滚动、点击或按键会取消尚未完成的跳转或阅读位置恢复，迟到的响应不会替换你正在阅读的窗口。关闭面板时会保存最后的阅读位置。重新连接后会继续自动加载历史，无需等待之前的重试延迟。

## 重启后继续执行

关闭会话标签页或 App 窗口后，由 worker 管理的任务继续运行。如果 worker 本身停止，可恢复的 Agent 任务会使用模型调用和工具执行完成时保存的 checkpoint。重启后，因关闭而暂停、或从安全 checkpoint 恢复的中断任务，在两小时内自动继续。期限持久化保存，再次重启不会延长期限。超时后任务保持暂停，可以手动继续。

`execution.auto_resume_window_seconds` 默认 `7200`，设置为 `0` 可禁用自动恢复。明确停止或取消、手动暂停、尚未回答的审批不会自动继续。外部操作结果无法确认时，需要先核对结果。恢复保留原任务身份、权限和资源准入要求，并要求运行契约兼容。恢复对象是持久化 checkpoint，不包括任意进程内存或外部应用尚未保存的状态。

自更新后的继续执行、运行契约变化后的重新规划和原会话唤醒也使用此设置，从已结束更新最后记录的活动时刻计算。持续运行的更新不会被这一恢复计时器停止。

### 共享 Terminal 环境

持久 Terminal 环境关联到会话后，与网页一起显示在 Resources 列表中。点击终端可在会话旁打开交互视图；用户和 Agent 操作的是同一个 shell，工作目录、环境变量与输出保持一致。隐藏视图和切换会话不会终止进程。“终止终端”需要确认；请求已接收不代表进程已经退出。“共享终端”允许本机其他会话发现它；改为私有会撤销当前 Agent 绑定。

输入已送达不代表命令成功。输入结果不确定时不会自动重发。“重新连接视图”仅重新读取输出。用户尚未提交的输入片段或输入版本变化会阻止 Agent 继续冲突操作，Agent 必须重新观察。

### Agent 统一资源接口

`resource` 工具通过 `describe` 返回 provider 及其支持的操作和参数结构。先调用 `resource(action="describe")`，再按返回的结构调用。内置 `web` 和 `terminal` 保留原有资源标识和检查；现有 `web_use`、`terminal_use` 继续兼容。例如 `resource(provider="terminal", action="list")` 列出可用 shell，`observe` 返回后续操作需要的 generation、binding、input revision 和输出 cursor。

只允许调用 provider 声明的操作。网页 `release` 释放控制会话而保留网页，`close` 使用已观察实例的控制绑定关闭准确的网页；终端 `release` 保留进程，`close` 请求终止进程。Terminal 需要来自对应 Desktop 窗口的有效执行，不能绕过任务 sandbox。

可信 Python 集成可以通过 `openprogram.resource_interface.registry.register(...)` 注册 `ResourceProvider`，为每种操作声明 JSON Schema 和调用适配器。适配器负责自身的授权和生命周期。重复名称会被拒绝，参数在调用前验证。这是可信集成 API，不接受 Agent 或网页提供任意代码。集成可以通过已有 `openprogram.session_resources.resource_use(...)` 在 Resources 上报会话使用情况；已启用的安装应用根据操作清单自动注册 `application.<id>`。关联当前会话的实例显示在 Resources，并复用现有隔离应用视图。
