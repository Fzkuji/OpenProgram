# 常见问题

这页收集安装和日常使用中最常见的问题，每条都给出对应的命令解法。

## 端口 18100 或 18109 被占用怎么办？

先看当前配置的端口，再改成空闲的：

```bash
openprogram ports                              # 查看当前端口
openprogram ports --backend 18119 --frontend 18110   # 持久修改，下次启动生效
```

只想改一次运行，用环境变量 `OPENPROGRAM_BACKEND_PORT` / `OPENPROGRAM_WEB_PORT` 覆盖。如果占端口的是残留进程，`lsof -ti:18100 | xargs kill` 释放后重启。

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

```bash
openprogram update           # 检查并应用更新
openprogram update --check   # 只检查，不应用
openprogram update --force   # 绕过 6 小时节流，立即检查
```

worker 启动时也会在后台自动检查更新（每 6 小时至多一次）。详见 [升级](../install/upgrade.md)。

## `openprogram web` 打开的页面加载不出来？

打开的必须是 **http://localhost:18100**（前端），不是 :18109（后端 API，没有 HTML 页面）。如果 18100 上什么都没有，多半是 web UI 没构建——重新运行 `./scripts/install.sh` 即可。

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

## GUI agent 下载太慢或失败了怎么办？

`openprogram programs install gui` 会下载 PyTorch（CPU 版约 300 MB，CUDA 机器约 3 GB）和模型权重，耗时正常。失败后重跑同一条命令即可续装。GPA 检测权重下不动时可手动获取：

```bash
hf download Salesforce/GPA-GUI-Detector model.pt --local-dir ~/GPA-GUI-Detector
```

## 装完 agent 程序后界面里没出现？

程序在启动时注册，装完后需要 `openprogram restart`（或在 Functions 页面点 Refresh）。用 `openprogram programs available` 确认它已安装。

## 同一个 provider 有多个账户或多个 key，怎么切换？

```bash
openprogram providers login openai --profile work   # 添加第二个账户
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
