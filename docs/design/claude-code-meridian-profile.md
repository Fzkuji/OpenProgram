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

`openprogram/providers/openai_completions/openai_completions.py` 的
`stream_simple()` —— **claude-code 所有请求的唯一必经点**。claude-code 的
model `api="openai-completions"`,所以不论请求经由 `providers/stream.py`
的统一 wrapper、还是被某些调用方(如 `memory/llm_bridge.py` 直接调
`api_provider.stream_simple`)绕过 wrapper,最终都落到这个函数。

> 设计评审纠正:最初把注入放在 `providers/stream.py` 的 wrapper 里,但对抗
> 审查发现 `memory/llm_bridge.py` 直接调 raw api-provider、绕过 wrapper,
> 于是 memory summarization 在默认模型是 claude-code 时仍不带 header、泄漏
> 到终端登录账号 —— 正是本设计要消除的耦合。改放 openai_completions 这个
> chokepoint 后覆盖全部路径。

注入逻辑封装在 claude-code 模块的
`_claude_max_proxy_registry.inject_profile_header(model, headers)`(不把
claude-code 专属逻辑塞进通用层),`openai_completions` 仅在
`model.provider == "claude-code"` 时惰性 import 并调用它。由于只有
`api="openai-completions"` 的 model 才会进 openai_completions,将来即便重新
启用 `claude-code-cli`(另一种 wire)的同名 provider 模型,也不会被误注入。

### 立即生效

`stream_simple` 每次请求都读配置(经一个 `_meridian_profile()` 读
`_read_config()`),所以 WebUI 改了 profile **下一次请求就生效,不用重启
worker**。`opts` 用 `model_copy` 注入,不改原对象,无副作用。

### 优先级细节

若调用方自己已经在 `opts.headers` 里放了 `x-meridian-profile`(几乎不会),
尊重调用方的(per-call 比全局配置更具体)。即:
`{"x-meridian-profile": profile, **(opts.headers or {})}`。

### `meridian_profile()` / `inject_profile_header()` 落点

放在 `openprogram/providers/anthropic/_claude_max_proxy_registry.py`
(claude-code 相关模块已经在这),`openai_completions` 惰性 import 以避免
循环。`meridian_profile()` 对非字符串配置值做 `str()` 兜底,免得手改
config.json 写错类型时在 `.strip()` 抛错被静默吞掉。

### 已知限制

- **不校验 profile 是否真实存在**:Meridian 没有 JSON profiles API,这一层
  无法在发请求前确认 `meridian_profile` 名字有效。名字错了由 Meridian 决定
  行为(报错→正常 API error,或静默回落到它的 default = 可能用错账号)。
  P1 的 WebUI picker 用 `meridian profile list` 把可选值约束到已知 profile,
  从源头堵住这个问题。
- **每请求读一次 config.json**(~0.3ms,被代理+模型网络往返淹没)。为支持
  "WebUI 改了立即生效"而不缓存;若日后成为热点,可按 mtime 记忆化。

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

- 单元(`tests/unit/test_claude_code_meridian_profile.py`,10 passed):
  `inject_profile_header` 在 claude-code + pin 时加 header,未配 / 其他
  provider / caller 自带 header 时的行为;`meridian_profile()` 的
  config > env > None 优先级与非字符串兜底;集成测试确认 claude-code 经
  `openai_completions.stream_simple` 时 header 真的进了 openai client 的
  `default_headers`。
- 端到端(P1/P2 配好 profile 后):从 OpenProgram 实跑一次 claude-code,确认
  Meridian 路由到该 profile 的账号;`claude auth status`(终端)仍是另一个
  账号、不受影响。

## 跨平台账号添加(Windows 支持)

Web 端「添加 Claude 账号」原本只在 POSIX 可用(用 stdlib `pty` 驱动 Meridian
的交互式 `profile add`)。Windows 没有 `pty`,旧代码直接报错(且更早一步会卡在
无超时的 `npm install`)。现在两种登录方式都支持,且都先检测并引导安装前置工具。

- **前置检测 / 引导**:`_meridian_cli.prerequisites()` 报告 `claude_installed` /
  `backend_installed` / `browser_login`(= 是否有可用伪终端)/ `token_login`。
  两种登录都最终经 **Claude Code CLI** 完成 OAuth(Meridian 内部 `spawnSync`
  `claude auth login`;token 方式靠 `claude setup-token` 生成 token),所以 UI 在
  `claude` 缺失时提示 `npm install -g @anthropic-ai/claude-code` 并提供「重新检测」。
- **浏览器登录(与 mac/Linux 一致)**:`_compat.InteractivePty` 抽象伪终端 ——
  POSIX 用 `pty`,Windows 用 **ConPTY(`pywinpty`,可选 Windows-only 依赖)**,
  读 URL / 写 code 的逻辑两端一致(Windows 的 `write()` 把 `\n` 译成 `\r\n`,
  因为 ConPTY 靠 CR 完成一行)。`pywinpty` 不可用时该方式禁用,回退 token。
- **Token 粘贴(headless、全平台)**:`add_with_token()` 跑
  `meridian profile add NAME --oauth-token <token>`,纯非交互子进程,任何系统都行。
  代价:setup-token 无 refresh token,不自动续期(~1 年),且账号无 email、显示为
  `account-N`。
- **其它 Windows 修复**:`_proxy_bin()`/`_npm_bin()` 在 PATH 之外再查 npm 全局前缀
  (`%APPDATA%\npm`),否则刚 `npm install` 的 backend 找不到;`_run_proxy` /
  `add_with_token` 用 `encoding="utf-8"` 解码(默认 gbk 会被 backend 的智能引号
  噎到);Meridian 守护进程用 `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` 真正
  脱离(`start_new_session` 在 Windows 是 no-op);`ensure_backend` 的 npm 安装加
  300s 超时,前端 fetch 加 AbortController 超时,避免「点了没反应」。

> 无法在本仓库环境端到端验证的部分:真正的浏览器 OAuth 经 Windows ConPTY 驱动
> `claude`(需装 Claude Code CLI + 真实 Claude Max 登录)。`InteractivePty` 的
> 读写/超时/收尾已用 dummy 子进程在 Windows 实测;token 路径的子进程接线已用 fake
> token 实测(Meridian 接受并建 profile,再用时校验)。
