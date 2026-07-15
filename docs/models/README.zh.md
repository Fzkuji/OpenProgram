# 配置模型

OpenProgram 需要至少一个 LLM provider 才能工作。本页说明首次配置、provider 管理命令和"启用模型"的机制。

## 首次配置

```bash
openprogram setup
```

首次运行向导逐节走完模型、工具、agent 等配置；`openprogram setup model` 只跳到模型一节，`openprogram setup menu` 打开交互式选单。Web UI 首次打开时也会弹同一套向导（Settings → Providers 随时可改）。

只配 provider 凭据可以用更窄的入口：

```bash
openprogram providers setup      # 交互式：扫描现有凭据 → 登录 → 验证
```

## providers 子命令

`openprogram providers -h` 列出全部动词，常用：

| 命令 | 作用 |
|---|---|
| `login <provider>` | 登录一个 provider。自动选择合适的方式（OAuth 或 API key）；脚本里可用 `--api-key-stdin` |
| `logout <provider>` | 删除该 provider 的凭据 |
| `status <provider>` | 检查当前凭据是否可用 |
| `list` | 按 profile 列出已配置的凭据池 |
| `available [QUERY]` | 列出全部可配置的 provider 目录（含社区目录），可按关键词过滤 |
| `discover` / `adopt` | 扫描本机已有凭据（Codex CLI、环境变量等）并导入，见[认证与凭据](auth.md) |
| `use <provider> [profile]` | 多账号时选择该 provider 当前跑哪个账号 |
| `doctor` | 诊断凭据：过期、刷新、冷却、冲突 |
| `aliases` / `profiles` / `migrate` | 短名别名、账号 profile 管理、凭据格式迁移 |

## 启用模型（enabled models）

配置好凭据不等于模型可选。每个 provider 有一份"已启用模型"清单，只有启用的模型才出现在聊天界面的模型选择器里。

- 机制：注册表（`openprogram/providers/enabled_models.py`）在启动时从配置文件读取每个 provider 下的模型行，构建运行时模型清单；改配置后重载生效。
- 配置文件：`~/.openprogram/config.json`（使用 `--profile <name>` 时为 `~/.openprogram-<name>/config.json`）。每个 provider 记在 `providers.<id>` 下：`enabled` 开关和 `models` 下启用的模型行（含上下文窗口、价格等 spec）。手动添加的模型也存在同一清单里，标记 `source: "manual"`。
- 启用途径：setup 向导勾选；Web UI 的 Settings → Providers 里浏览该 provider 的模型列表后勾选（Fetch 按钮重新拉取官方模型目录）；订阅类 provider 登录时自动启用默认模型集。一般不需要手改 config.json。
- 默认模型：`default_provider` 和 `default_model` 两个顶层配置项决定新会话用哪个模型；界面里切换模型即更新。

## 本节其他页面

- [Provider 一览](providers.md) — 内置 provider 目录、接入方式、库方式使用
- [认证与凭据](auth.md) — 凭据来源、存放位置、从其他 CLI 导入
- [fast tier](fast-tier.md) — 把请求路由到更快档位
- [thinking effort](thinking-effort.md) — 推理深度档位
- [Token 统计](token-tracking.md) — 各 provider 的用量统计口径
