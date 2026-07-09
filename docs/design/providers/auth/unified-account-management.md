# 统一账号管理 + 凭据池轮换/回退

**目标（用户）：** 在 CLI / web / TUI 之间提供一种一致的账号管理方式——
对每个 provider 列出 / 添加 / 激活 / 重命名 / 删除多个账号，外加带开关的
轮换 / 故障转移。每个 provider 背后的*后端*（claude-code 用 Meridian，
其余用 AuthStore）只是实现细节；**管理界面是统一的**。本设计基于
docs/design/providers/auth/unified-auth-storage.md（登录侧，P1）。

## 核心思路

一个**账号 = 一个具名 profile**。AuthStore 已经以
`(provider_id, profile_id)` 为键管理每一个凭据池，并为每个池持久化一个文件
（`~/.openprogram/auth/<provider>/<profile>.json`），而 `ProfileManager` 也
已经实现了 profile 的 CRUD。所以"多账号"和"多 profile"是同一个
概念——每个账号就是一个 profile id。claude-code 的 `ClaudeAccounts` 面板
（列出 / 通过登录添加 / 激活 / 重命名 / 删除）就是要
**推广到每个 provider** 的 UX 模板；claude-code 只是它的一个实例。

## 已有的部分（不要重建）

- 多 profile 存储 + `ProfileManager` CRUD（`auth/store.py`、`auth/profiles.py`）。
- 凭据池策略模型——`PoolStrategy = fill_first | round_robin | random |
  least_used`、`credentials[]`、`_rr_cursor`、`fallback_chain`、每个凭据的
  `cooldown_until_ms` / `status`——全部已序列化（`auth/types.py:335-390`）。
- 遵循策略 + 健康过滤 + 跳过冷却的凭据池选择
  （`auth/pool.py:99-161`）；回退递归（`auth/manager.py:247-312`）；
  冷却时长 + `mark_failure`/`mark_success`/`clear_cooldown`
  （`auth/pool.py:57-276`）；manager 封装 `report_failure`/`report_success`
  （`auth/manager.py:450-497`）。
- 一套更丰富的多 profile REST 界面已经挂载（`webui/_auth_routes.py`：
  `/profiles`、`/pools`、`/pools/.../credentials`、`/doctor`、SSE `/events`）——
  目前只被一个页面使用。
- P1 提供的统一登录端点（`/api/providers/{id}/login/{start,poll,
  submit,cancel}`）+ `<ProviderLogin>`。

## 两个关键缺口（先修这两个——否则其余都只是表面文章）

1. **请求时没有激活 profile 的选择。** `AuthManager.acquire`
   默认 `profile_id="default"`，且请求路径从不进入
   `auth_scope(...)`，所以用户实际上无法在"工作"和"个人"之间切换运行。
   *唯一*能用的激活账号选择器是 claude-code 的，它通过
   provider-config 值 `meridian_profile` 实现。需要：一个通用的
   `get/set_active_profile(provider_id)` + 让 `acquire`/`resolver` 默认使用
   它 + 让 chat/execute 入口进入该 scope。
2. **轮换/冷却/回退从不生效。** `report_failure` / `report_success`
   在其定义之外**没有任何调用方**——没有任何 provider 运行时把
   429/402/5xx 反馈给凭据池，所以 `cooldown_until_ms` 一直为 0，`fill_first`
   永远返回 #0 凭据，轮换/回退形同虚设。需要：运行时在失败时调用
   `manager.report_failure(...)`，在 2xx 时调用 `report_success(...)`。

（另外：第二次 OAuth 登录会被 `_prune_superseded_oauth` 裁掉，所以 OAuth
凭据池无法积累两个凭据——这对*API key* 之间的轮换没问题，那才是真实场景；
OAuth 多账号由多个 *profile* 来处理。）

## 目标架构

- **激活账号 = 激活 profile**，可按 provider 设置，由运行时遵循（缺口 1）。
- **通用账号 REST** `/api/providers/{id}/accounts/*`，与 claude-code 的形状
  对齐，从而让前端可以原样复用：
  `GET …/accounts` → `{active, accounts:[{name,label,email?,status,kind}]}`、
  `POST …/accounts/use {name}`（""=取消激活）、`…/rename {old,new}`、添加（复用
  `/login/start|poll|submit` 并带上目标账号名）、删除（复用现有的
  凭据/池删除）。claude-code 在*相同*的路由后面保留其基于 Meridian 的
  实现（适配器），所以 UI 不需要分支。
- **一个 `<ProviderAccounts>` React 组件**（由
  `claude-accounts.tsx` 推广而来）+ **一个 Ink 选择器**（由
  `claudeAccounts.tsx` 推广而来），每个 provider 都复用。`detail.tsx` 为
  所有 provider 渲染它；TUI `/login <prov>` 不再甩锅给 web。
- **凭据池控制**——`PATCH /api/providers/pools/{prov}/{prof}` 用于设置
  `strategy` + 开关 `fallback_chain`、`…/clear_cooldown`（"立即重试"）、一个
  `…/health` 视图；在 web/TUI 中以策略下拉框 + 回退开关 + 每个凭据的
  健康徽标呈现，并提供 `providers pool {strategy,fallback,retry}` CLI
  动词。只有在缺口 2 接通之后才有意义。

## 分阶段计划

- **P-A——激活 profile 基础设施** ✅ 已完成（commit 836a4a9b、d8454d6a）。
  `auth/active.py`（`get/set_active_profile(provider)` + `get_active_pin`）；
  `acquire`/`resolver` 默认使用它；CLI `providers use <provider> [profile]`
  + `providers list` 中的 `← active` 标记。默认仍为 `"default"`，完全
  向后兼容。
- **P-B——通用账号界面 + 统一 UI** ✅ 已完成（commit 45cac805、
  4c98adcc、bedb3439）。
  - 后端：`routes/accounts.py` 提供 `/api/providers/{id}/accounts/*`
    （从 AuthStore 进行 list/use/rename/remove；添加则把登录方法
    交给 UI）。claude-code 保留其字面的 Meridian 路由（先注册
    以便覆盖 `{provider}`）；两者都上报 `add_mode`（`code_paste` 对
    `login`）。
  - Web：一个 `<ProviderAccounts>`（由 `claude-accounts.tsx` 推广而来）
    为 claude-code + 仅登录的 provider 渲染；`<ProviderLogin>` 新增了
    `profileId`/`bare`，使其成为内嵌的"添加账号"步骤。
  - TUI：一个通用选择器（`providerAccounts.tsx`）+ 一个 TUI 内的登录流程
    （`providerLoginFlow.tsx`），驱动共享的 `/login/*`；`/login <provider>`
    为任意 provider 打开它（不再甩锅给 web）。
- **P-C——轮换/故障转移接线 + 开关** ✅ 已完成（commit e0b04aa0、
  ac9b7f53、adbbf8af）。
  - P-C1 接线（`auth/usage.py` + `openai_completions.stream_simple`）：调用
    路径按请求从凭据池获取并上报结果
    （`report_failure`/`report_success`），所以 429 会让一个 key 进入冷却，外层
    重试则轮换到下一个。带门控——除非该 provider 有真实的
    AuthStore 凭据池，否则为空操作，所以 env-key / OAuth / claude-code 逐字节不变。
  - P-C2 控制界面（`routes/accounts.py`）：`GET …/{name}/keys`（脱敏 +
    每个 key 的健康 + 策略）、`POST …/{name}/strategy`、`…/{name}/retry`
    （"清除冷却"）、`POST/DELETE …/{name}/keys`（添加/删除一个 key）；
    账号记录新增了 `strategy` + `cooling`。
  - P-C3 web（`pool-controls.tsx`）：api-key provider 上的"Keys & rotation"
    面板——每个 key 的健康徽标、策略下拉框、"立即重试"、添加/删除。

  剩余项（次要，不阻塞主任务）：UI 中的 `fallback_chain` 开关；
  TUI 凭据池控制（web + REST + 经 REST 的 CLI 已覆盖）；原生
  `providers pool …` CLI 动词。

## 后端（claude-code 仍用 Meridian）

claude-code 继续以 Meridian 作为后端；它被适配到统一的
`/accounts/*` 路由后面，使其管理 UX 与其他所有 provider 一致。（设计
流程已确认，一条原生的 AnthropicRuntime+OAuth 路径同样存在并可工作——
`utils/oauth/anthropic.py` + `anthropic.py` 中的 OAuth-token 供给
——所以 claude-code 日后*可以*在不改变 UX 的情况下抛弃 Meridian；那是
一项可选的未来简化，在此明确不在范围内，因为后端原则是"怎么能用怎么来"。）

## 不破坏现有行为的护栏

- `default` profile 在各处仍为默认；激活其他 profile 是
  可选的。可用的 yzhang6294 claude-code 账号不受影响（仍用 Meridian）。
- P-A 发布时沿用现有行为（active 默认为 "default"）；P-C 的
  开关在选择 fill_first 以外的策略之前都处于惰性状态。

## P-D——为每个 provider 提供一个管理组件（UI 统一）

**问题（用户）：** api-key provider 和登录 provider 显示了*不同*的
面板（`<ProviderKeys>` 对 `<ProviderAccounts>`）——不同的布局、标签、
交互。这种差异是偶然的（是我分开写的两个组件），并非
必要。每个 provider 真正不同的只有**怎么添加**（粘贴 key / 登录 /
粘贴一个 code）和**一个身份长什么样**（一个脱敏的 key / 一个邮箱）。

**模型。** 每个 provider 都有**账号** = 具名、可切换的凭据：
- api-key provider → 一个账号是一个 **key**（id = credential_id，身份 =
  脱敏的 key）。
- 登录 provider（codex / copilot / gemini-sub）→ 一个账号是一次 **登录**
  （id = profile_id，身份 = 邮箱）。
- claude-code → 一个账号是一个 **Claude 订阅**（Meridian profile）。

各处统一的操作：**重命名**、**Use**（切换激活项）、
**删除**，以及一个可选的**轮换开关**（默认关闭；开启 = 在账号之间进行
限流故障转移，前提是后端支持）。只有**添加**会分支：
粘贴 key（+校验）/ 共享的登录流程 / 粘贴 code。

**形态。** 一个 React `<AccountManager driver={…}>` 渲染列表 + 轮换
开关 + 添加区域；每个后端有一个轻量的 **driver** 提供数据以及
use/rename/remove/rotation 调用，封装现有的端点（api-key →
`…/accounts/default/keys*`；login/claude-code → `…/accounts*`）。后端不需
重构——claude-code 仍用 Meridian。`detail.tsx` 为每个 provider 恰好渲染一个
`<AccountManager>`；`<ProviderKeys>` / `<ProviderAccounts>` /
独立的 `<ProviderLogin>` 都坍缩进它。

（未来，可选：把 api-key 的账号也提升为 profile + 跨 profile 轮换，
这样后端也是统一的，而不只是 UI。）

### P-E——一个后端模型：账号 = profile（由 OAuth 约束所强制）

UI 统一（P-D）把一个组件套在了两个*不同*的后端模型之上
（api-key = 一个池里的多个凭据；login = 每个 profile 一个凭据）。
用户说得对，那不是真正的统一。决定性的约束：
`_prune_superseded_oauth`——OAuth refresh-token 轮换意味着**一个池里最多只能
存活一个 OAuth 凭据**，所以 OAuth 多账号*必须*是分开的
profile。因此唯一覆盖每个 provider 的模型是**账号 =
profile**（每个 profile 持有一个凭据）。login、claude-code（Meridian
profile）和 P-A 的 `set_active_profile` 已经在用它；api-key 的 key 也搬到
它上面——每个具名 key 都是一个 profile。

- **切换**激活账号 = `set_active_profile`（P-A），各处通用。
- **轮换** = 一个按 provider 的开关，在该 provider 的 profile 之间轮换
  （429 时让一个 profile 的凭据进入冷却、跳过它、继续）；关闭 ⇒ 仅使用
  激活的 profile。它位于 `auth/usage.acquire_pooled` + 一个按 provider 的
  轮换设置中，所以热路径 `manager.acquire` 不受影响。
- 对 profile 凭据的**按账号操作**：reveal（显示完整 key）、
  update（替换它）、validate（只探测这一个）+ validate-all。
- api-key **添加** = 创建一个 profile + 添加 key；login 添加 = 共享的登录
  流程并带 `profile=<name>`（已实现）。claude-code = Meridian（适配器）。

随后 web + TUI 渲染这一个模型；api-key 的池内凭据界面
（`…/accounts/default/keys*`、池的 `active_credential_id`/`fixed`）退役。
