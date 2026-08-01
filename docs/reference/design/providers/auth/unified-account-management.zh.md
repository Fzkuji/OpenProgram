# 账号管理与凭据池轮换

CLI、web、TUI 用同一套方式管理账号：列出、添加、激活、重命名、删除，每个
provider 可以有多个账号，另有可开关的轮换与故障转移。某个 provider 背后是什么
后端属于实现细节，管理界面是统一的。本文建立在
[unified-auth-storage.md](./unified-auth-storage.md) 描述的登录侧之上。

## 一个账号就是一个 profile

AuthStore 以 `(provider_id, profile_id)` 为键存放每个凭据池，每个池一个文件，
位于 `~/.openprogram/auth/<provider>/<profile>.json`，而 `ProfileManager` 已经
提供 profile 的 CRUD。因此"多账号"与"多 profile"是同一个概念 —— 每个账号就是
一个 profile id。

强制采用这个模型的约束是 OAuth refresh-token 轮换：`_prune_superseded_oauth`
意味着**一个池里最多只能存活一个 OAuth 凭据**，所以 OAuth 多账号必须是分开的
profile。既然这个模型同样适用于 api-key provider，那么"账号 = profile"就是唯一
覆盖所有 provider 的模型，每个具名 api-key 也是一个 profile。

## 凭据池已经提供的能力

它所依托的存储与轮换机制：

- 多 profile 存储与 `ProfileManager` CRUD（`auth/store.py`、`auth/profiles.py`）。
- 凭据池策略模型 —— `PoolStrategy = fill_first | round_robin | random |
  least_used`、`credentials[]`、`_rr_cursor`、`fallback_chain`，以及每个凭据的
  `cooldown_until_ms` / `status`，全部已序列化（`auth/types.py:335-390`）。
- 遵循策略、健康过滤与冷却跳过的池选择（`auth/pool.py:99-161`）；fallback
  递归（`auth/manager.py:247-312`）；冷却时长与
  `mark_failure` / `mark_success` / `clear_cooldown`（`auth/pool.py:57-276`）；
  以及 manager 封装 `report_failure` / `report_success`
  （`auth/manager.py:450-497`）。
- 一套多 profile REST 界面（`webui/_auth_routes.py`：`/profiles`、`/pools`、
  `/pools/.../credentials`、`/doctor`、SSE `/events`）。
- 统一登录端点（`/api/providers/{id}/login/{start,poll,submit,cancel}`）
  与 `<ProviderLogin>`。

## 让凭据池真正生效的两处连接

轮换与按账号选择需要两处连接才不是空转，缺了它们，其余界面都只是摆设：

1. **请求时的激活 profile 选择。** `AuthManager.acquire` 默认
   `profile_id="default"`，因此除非请求路径进入 `auth_scope(...)`，用户无法真正
   以"work"而非"personal"运行。`auth/active.py` 提供
   `get_active_profile(provider)` / `set_active_profile(provider)` 以及
   `get_active_pin`；`acquire` 与 resolver 以它为默认，chat/execute 入口进入
   该作用域。默认仍是 `"default"`，激活其他 profile 是可选行为。
2. **调用路径回报结果。** `report_failure` 与 `report_success` 只有在 provider
   runtime 把 429/402/5xx 回报给凭据池时才有意义。没有这一步，
   `cooldown_until_ms` 恒为 0，`fill_first` 永远返回第 0 个凭据，轮换与 fallback
   都不会启动。调用路径（`auth/usage.py` 与 `openai_completions.stream_simple`）
   按请求从池中获取凭据并回报结果，于是一次 429 会让一把 key 进入冷却，外层重试
   轮换到下一把。

回报是带门控的：除非该 provider 拥有真实的 AuthStore 凭据池，否则它是 no-op，
因此 env-key 与 OAuth 路径不受影响。

## 管理界面

**REST。** `/api/providers/{id}/accounts/*` 是通用的：
`GET …/accounts` 返回 `{active, accounts:[{name,label,email?,status,kind}]}`；
`POST …/accounts/use {name}` 激活某一个（`""` 表示取消激活）；`…/rename
{old,new}`；添加复用 `/login/start|poll|submit` 并带上目标账号名；删除复用既有的
凭据/池删除。每个 provider 都上报一个 `add_mode`（`code_paste` 或 `login`），
这样前端无需按 provider 身份分支。

**凭据池控制。** `GET …/{name}/keys` 返回脱敏后的 key、每个 key 的健康度和当前
策略；`POST …/{name}/strategy` 设置策略；`…/{name}/retry` 清除冷却；
`POST`/`DELETE …/{name}/keys` 添加与删除一把 key。账号记录携带 `strategy`
与冷却状态。

**每个界面一个组件。** web 为所有 provider 渲染同一个 `<AccountManager
driver={…}>` —— 列表、轮换开关、添加区域 —— 每个后端有一个轻量 driver，通过
上述端点提供数据以及 use/rename/remove/rotation 调用。TUI 有一个通用选择器
（`providerAccounts.tsx`）和一个 TUI 内登录流程（`providerLoginFlow.tsx`）驱动
共享的 `/login/*`，因此 `/login <provider>` 对任何 provider 都可用，无需跳转到
web。

只用一个组件的理由是：api-key provider 与登录 provider 只有两处不同 ——
**怎么添加**（粘贴 key、登录，或粘贴一个 code）以及**一个身份长什么样**
（脱敏的 key 或邮箱）。其余的重命名、Use、删除、可选的轮换开关都是统一的，
因此分成 `<ProviderKeys>` 与 `<ProviderAccounts>` 两个面板没有理由。

## 按账号的操作

各 provider 统一：**重命名**、**Use**（切换激活账号）、**删除**，以及一个可选的
**轮换开关**，默认关闭。开启后，轮换在该 provider 的各 profile 之间进行 ——
429 时让某个 profile 的凭据进入冷却、跳过它、继续；关闭则只使用激活的 profile。
轮换位于 `auth/usage.acquire_pooled` 与一个按 provider 的轮换设置中，热路径
`manager.acquire` 不受影响。

对 profile 的凭据可以：reveal（显示完整 key）、update（替换）、validate
（只探测这一个），以及 validate-all。

只有**添加**按后端分支：api-key 添加会创建一个 profile 并加入 key；登录添加是
共享登录流程并带 `profile=<name>`。

## 实现状态

已就位：激活 profile 的基础设施（`auth/active.py`、CLI
`providers use <provider> [profile]`、`providers list` 中的 `← active` 标记）；
通用账号 REST 界面与统一的 web、TUI 组件；以及轮换的接线与其控制界面
（`routes/accounts.py` 的 strategy/retry/keys 端点，`pool-controls.tsx` 中的
"Keys & rotation" 面板）。

尚未完成：UI 中的 `fallback_chain` 开关；TUI 凭据池控制（web、REST 以及经 REST
的 CLI 已覆盖同样的操作）；原生 `providers pool …` CLI 动词；以及在"账号 =
profile"成为模型之后，退役 api-key 的池内凭据界面
（`…/accounts/default/keys*`、池的 `active_credential_id`/`fixed`）。
