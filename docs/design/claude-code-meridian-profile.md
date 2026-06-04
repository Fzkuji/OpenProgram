# claude-code: 固定 Meridian 账号(profile),与终端 Claude Code 解耦

## 背景 / 问题

OpenProgram 的 `claude-code` provider 不直接持有凭证。它把请求用 OpenAI
兼容格式发到本地的 **Meridian** 代理(`localhost:3456`),Meridian 在本地
跑官方 Claude Code SDK,路由到某个 Claude 订阅账号的 OAuth session。

当 Meridian **没有配置 profile** 时,它用的是"`claude` CLI 当前登录的那个
账号"(系统钥匙串里那一个)。后果:

- 用户在终端 `claude auth login` 换一个账号(给 Claude Code 用),
  钥匙串被覆盖,Meridian 跟着变,**OpenProgram 用的账号也跟着变**。
- 两者强耦合,无法做到"Claude Code 用账号 B 聊天、OpenProgram 用账号 A
  跑实验"这种分工。

典型场景:账号 A 是实验号(额度周期性用尽又恢复),账号 B 是常规号。用户
希望 OpenProgram 永远固定在 A,无论终端 Claude Code 当前登录的是谁。

## 目标

1. OpenProgram 的 `claude-code` 固定绑定**一个指定的 Meridian profile**,
   与终端 `claude auth login` 的登录状态**完全解耦**。
2. 切换 OpenProgram 用哪个账号,在 OpenProgram 设置(或 Meridian)里一个
   明确的地方改 —— 不受外部 Claude Code 换登录影响。
3. 安装 OpenProgram 时自动安装 / 引导配置 Meridian,降低新用户门槛。

## 解耦的技术核心

Meridian 支持 named profiles,每个 profile 用隔离的 `CLAUDE_CONFIG_DIR`
(独立登录目录,**不碰钥匙串**)。选择 profile 的方式里,**per-request
header `x-meridian-profile: <name>` 是最高优先级**,会盖过钥匙串当前登录、
也盖过 Meridian 全局 default。

所以解耦 = **让 OpenProgram 的每个 claude-code 请求都固定带上
`x-meridian-profile: <绑定的 profile>`**。从此:

- OpenProgram 永远锁定那个 profile(那个账号的独立登录目录);
- 终端 Claude Code 不经过 Meridian(它是官方 CLI,直接用钥匙串),怎么换
  登录都碰不到 OpenProgram 这条;
- 两条彻底分开。

## 方案总览(四块,分阶段)

| 阶段 | 块 | 内容 |
| --- | --- | --- |
| P0 | 绑定解耦(核心) | claude-code 请求固定带 `x-meridian-profile`,profile 名来自配置 |
| P1 | WebUI 配置入口 | claude-code 面板选/切 profile,后台 `meridian profile list` 列出 |
| P2 | 自动装 + 引导 | 照搬 `agent-browser` 的 `npm install -g` 模式自动装 Meridian + 引导建 profile |

## P0 详细设计(本次实现)

### 配置项

新增 `config.providers.claude-code.meridian_profile`(字符串,profile 名)。
解析优先级:

1. `config.providers.claude-code.meridian_profile`(WebUI 写这里)
2. 环境变量 `CLAUDE_MAX_PROXY_PROFILE`,别名 `MERIDIAN_PROFILE`
3. 都没有 → 不带 header(回到旧行为:Meridian 用它自己的 active/default
   profile 或钥匙串当前登录)

### 注入点

`openprogram/providers/stream.py` 的 `stream_simple()` —— 所有 provider 的
统一入口,已经在这里做 per-provider 处理(注入 api_key)。在调
`provider.stream_simple(...)` 之前,若 `model.provider == "claude-code"`
且解析出 profile,就把 `x-meridian-profile` 合并进 `opts.headers`。

选这里而不是 `openai_completions`(claude-code 走的 wire)的原因:

- `openai_completions` 是被很多 provider 共用的通用层,不该塞 claude-code
  专属逻辑;
- `stream.py` 已经按 `model.provider` 做事,语义一致;
- 注入到 `opts.headers` 后,`openai_completions` 现有的
  `extra_headers = opts.headers or {}` 直接发出去,**openai_completions
  无需改动**。

### 立即生效

`stream_simple` 每次请求都读配置(经一个 `_meridian_profile()` 读
`_read_config()`),所以 WebUI 改了 profile **下一次请求就生效,不用重启
worker**。`opts` 用 `model_copy` 注入,不改原对象,无副作用。

### 优先级细节

若调用方自己已经在 `opts.headers` 里放了 `x-meridian-profile`(几乎不会),
尊重调用方的(per-call 比全局配置更具体)。即:
`{"x-meridian-profile": profile, **(opts.headers or {})}`。

### `_meridian_profile()` 落点

放在 `openprogram/providers/anthropic/_claude_max_proxy_registry.py`
(claude-code 相关模块已经在这),`stream.py` 惰性 import 以避免循环。

## P1(后续)WebUI 配置入口

- claude-code provider 面板加 "Claude 账号(Meridian profile)" 选择框。
- 后台 `meridian profile list`(Meridian 无 JSON profiles API,只能 shell
  调)解析出 profile 列表供选择。
- 选定写入 `config.providers.claude-code.meridian_profile`。

## P2(后续)自动装 + 引导

- 照搬 `_cli_cmds/browser.py` 装 `agent-browser` 的模式
  (`npm install -g`,npm 缺失则 fallback 提示)自动装 `@rynfar/meridian`。
- setup 串联:装 Meridian → 引导 `meridian profile add <实验账号>` → 在
  OpenProgram 里选它。

## 验证

- 单元:`stream_simple` 对 `model.provider=="claude-code"` 且配了 profile
  时,`provider.stream_simple` 收到的 `opts.headers` 含
  `x-meridian-profile`;未配时不含;非 claude-code provider 永不受影响。
- 端到端:配 profile 后从 OpenProgram 实跑一次 claude-code,确认 Meridian
  路由到该 profile 的账号;`claude auth status`(终端)仍是另一个账号、
  不受影响。
