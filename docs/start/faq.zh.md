# 常见问题

这页收集安装和日常使用中最常见的问题，每条都给出对应的命令解法。

## 端口 18100 被占用怎么办？

OpenProgram 只监听一个端口（API、WebSocket 和 web UI 同端口，默认 18100）。先看当前配置的端口，再改成空闲的：

```bash
openprogram ports                    # 查看当前端口
openprogram ports --port 18110   # 持久修改，下次启动生效
```

只想改一次运行，用环境变量 `OPENPROGRAM_WEB_PORT` 覆盖。如果占端口的是残留进程，`lsof -ti:18100 | xargs kill` 释放后重启。

## provider 没被检测到 / "No provider available"？

```bash
openprogram providers            # 列出已检测到的凭据
openprogram providers discover   # 扫描外部来源（Claude Code / Codex / Gemini CLI 等）
openprogram providers doctor     # 诊断凭据：过期、刷新、冷却、冲突
openprogram setup                # 重新走一遍配置向导
```

也可以直接设置环境变量（`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`）后重启服务。

## 我的数据存在哪里？

默认全部在 `~/.openprogram/` 下：`config.json`（配置）、`sessions/`（会话）、`logs/`（日志）、`memory/`（记忆）、`usage.db`（token 用量）。使用 `--profile <name>` 时改存 `~/.openprogram-<name>/`。

## 怎么更新到最新版本？

stable installation 只变更到明确的已发布版本。0.7.0 是首个启用 updater 的版本，也是从 v0.6.6 进入 updater release 线的一次性过渡：macOS Desktop 用户手动安装 v0.7.0 DMG，原有 macOS/Linux CLI/server 用户重新运行一次公开 installer。当前 managed CLI/server release 还通过 PowerShell installer 支持 Windows x86_64。后续 Desktop release 在 Settings 中发现；managed CLI/server 和 source-checkout 用户都运行 `openprogram upgrade`，命令根据安装类型选择 release 路径或 Git 门禁路径。详见[升级](../install/upgrade.zh.md)。

## `openprogram web` 打开的页面加载不出来？

打开的是 **http://localhost:18100**，Web UI 和 API 共用该端口。release wheel 已包含 Web export；如果缺失，重新安装同一个 release。在 source checkout 中，运行开发 installer 重新构建。

## 服务好像没起来 / 行为异常，怎么排查？

按这个顺序：

```bash
openprogram status     # 服务是否在跑
openprogram restart    # 重启
openprogram doctor     # 健康检查
openprogram rescue     # 诊断问题并打印修复命令
```

## 怎么看日志？

```bash
openprogram logs list            # 所有日志文件（大小、时间）
openprogram logs tail            # 最后 50 行 worker 日志
openprogram logs tail -f         # 持续跟踪
openprogram logs tail runtime    # 指定日志：worker / runtime / ink
```

## 为什么 release 下载体积较大？

完整 release 已包含 managed Python、Playwright Chromium、GPA 检测模型权重，以及 GUI、Research、Wiki Program。不包含 PyTorch 或 EasyOCR。普通安装不会再单独下载这些产品组件。

## 可以安装 Chrome 或 Edge 浏览器扩展吗？

不可以。Chrome Web Store 与 Edge Add-ons 页面可以作为普通网页在内置浏览器中打开，但 OpenProgram 不增加扩展安装按钮，不下载 CRX，不从其他浏览器导入扩展，也不提供扩展管理页。应用使用标准 Electron/Chromium；Electron 只支持部分 Chrome Extensions API，也不以兼容任意 Chrome Web Store 扩展为目标。OpenProgram 不维护定制 Chromium/Electron 分支，也不为扩展兼容增加另一套浏览器运行时。release 内已有的 Playwright Chromium 只用于浏览器自动化后端，不承载 Desktop Browser Pane 或扩展。

需要扩展 OpenProgram 本身时，使用 [Plugins](../capabilities/plugins.zh.md)、Skills、MCP servers、Programs 或 agent tools。这些能力扩展的是 OpenProgram，不是内嵌网页运行时。

## 内置 agent Program 没出现在界面里？

先用 `openprogram programs available` 查看注册状态，再重启 OpenProgram 或在 Programs 页面点 Refresh。release 缺少第一方 Program 属于打包缺陷，不是需要用户另行安装的功能。

## 同一个 provider 有多个账户或多个 key，怎么切换？

```bash
openprogram providers login openai --account work   # 添加第二个账户
openprogram providers use openai work               # 切到 work 账户
openprogram providers list                          # 查看各账户，激活的有标记
```

## 一台机器能同时跑两个 OpenProgram 吗？

能，用 profile 把状态目录和端口分开，见 [多实例与 profile](../install/profiles.md)。

## 之前的对话怎么找回来？

```bash
openprogram sessions list          # 列出所有会话
openprogram --resume <session_id>  # 在终端续上
```

web 侧栏也能直接点开历史会话。
