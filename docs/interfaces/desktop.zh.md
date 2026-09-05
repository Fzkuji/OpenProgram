# 桌面 App 与内置浏览器

macOS 与 Windows Desktop App 将 OpenProgram 显示为多 Pane 工作区。每个 Pane 可以放 Files、聊天、内置 Browser 或 Terminal；Pane 可以分屏，也可以移到其他应用窗口，底层会话与浏览器 tab 不会因此改变。

macOS 从 [GitHub Releases](https://github.com/Fzkuji/OpenProgram/releases) 下载对应架构的 DMG，把 `OpenProgram.app` 复制到 `/Applications`；当前 macOS 渠道未签名，首次启动可能需要进入“系统设置 → 隐私与安全性 → 仍要打开”。Windows 只安装已发布 release 附带的带签名 `win-x64.exe` 或 `win-arm64.exe`；某个版本没有带签名 Windows EXE 时，使用该版本的 CLI/server 与浏览器 UI。完整步骤见[安装](../install/install.zh.md)。

Terminal Pane 在 macOS 使用 login shell，在 Windows 通过 ConPTY 使用 Windows PowerShell。两个平台的封装 App 都从内置 managed Python 启动 worker，不依赖系统 Python 或 Node.js。

## 打开 Browser

新建 Pane 后选择 **Browser**，或从应用 tab 栏新建浏览器 tab。Browser home 与已加载网页使用同一套浏览器控件：

- Back、Forward、Reload/Stop、Home、地址/搜索输入、收藏当前页、Bookmarks、外部打开和 Browser 菜单。
- 默认可见的书签栏；可以从 Browser 菜单或 Browser settings 隐藏。
- 响应式控件：Pane 变窄时，低频动作移入 Browser 菜单；地址输入、Back、Reload/Stop、收藏当前页和菜单仍然可用。

Browser 菜单只管理浏览器动作：新建浏览器 tab、Bookmarks、History、书签栏显示、profile 导入、清除浏览数据和 Browser settings。窗口与 Pane 操作仍归 OpenProgram 窗口菜单管理。

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

若 Agent 在你停留在聊天时打开页面，桌面端会把该实时 WebTab 显示为角落小窗。小窗可以展开为「左聊天右网页」分屏、接管中间栏，或关闭预览且不销毁标签。关闭预览只是隐藏小窗，页面仍留在标签栏。网页版（普通浏览器里的 UI，不是桌面 App）没有原生 BrowserView，同一预览降级为 iframe 或「在新标签打开」。

动作始终绑定发起聊天的窗口与 WebTab。默认使用 DOM、ARIA、页面文本和 element refs；只有视觉任务或结构化方式无法定位页面时，才使用一张当前 viewport screenshot。该路径不增加 OCR、object detector、多轮裁剪、component memory、vision memory 或 workflow replay。

## 浏览器扩展

Chrome Web Store 与 Edge Add-ons 页面可以作为普通网页打开，但 OpenProgram 不安装浏览器扩展，不下载 CRX，不从其他浏览器导入扩展，也不提供扩展管理页。应用使用标准 Electron/Chromium。Electron 只暴露部分 Chrome Extensions API，并明确不以兼容任意 Chrome Web Store 扩展为目标；OpenProgram 不维护定制 Chromium/Electron 分支，也不为扩展兼容增加另一套浏览器运行时。完整 runtime 内已有的 Playwright Chromium 只属于浏览器自动化后端，不承载 Desktop Browser Pane 或扩展。

需要扩展 OpenProgram 本身时，使用 [OpenProgram Plugins](../capabilities/plugins.zh.md)、Skills、MCP servers、Programs 或 agent tools。这些能力不修改内嵌网页运行时。

持续维护的工程规范见[内置浏览器设计](../reference/design/ui/built-in-browser.html)与 [Web Use / Computer Use 边界](../reference/design/integrations/web-use.html)。
