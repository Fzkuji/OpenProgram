# 凭证状态重新设计 —— “可用或停用”，去掉 COOLING 标记

## 问题

账号池把三种不同语义的失败压平成了一个 `cooldown_until_ms` 窗口，并在
UI 中以 COOLING 标记呈现：

- 一个 404（模型不存在）会把整把 key 冷却 30 秒 —— 因为一次错误请求而
  连累这把 key 上的所有其他模型；
- 一个 402（额度耗尽）会把 key “冷却” 24 小时 —— 但额度不会靠等待恢复，
  用户必须充值；
- 一个 5xx 会冷却 key —— 但上游故障跟这把 key 本身毫无关系。

按量付费的 API key 没有天然的“冷却”状态。它要么可用，要么因为某个需要
用户处理的原因而停用（充值 / 重新认证）。只有订阅/配额类账号（Claude
Pro 窗口、免费层模型）才真正具有“等到重置”的语义 —— 而这是与 key 健康
度完全不同的概念。

## 其他框架的做法

- **OpenClaw**（我们账号池的前身）保留三种独立状态：`cooldownUntil`
  （瞬时 429，阶梯式 30s→1m→5m）、`disabledUntil`（402 计费 / 永久认证
  失败 —— 禁用，指数退避），以及 `blockedUntil`（订阅配额，带来自 usage
  API 的真实重置时间戳）。5xx 从不影响 profile 健康度；OpenRouter 明确
  豁免于冷却。
- **opencode**：没有 key 池，没有冷却。错误是请求级的 —— 429/5xx 以指数
  退避重试两次，其余一切都作为结构化错误返回给调用方。
- **Claude Code**：单账号。每次失败都是聊天侧的一条消息，附带一个动作
  （402 → 充值，401 → /login，404 → /model，配额 → 重置倒计时 + 选项
  菜单）。设置界面不展示任何瞬时状态。

## 新模型

**用户可见状态（持久化，展示在账号面板中）：**

| status | 含义 | 恢复方式 |
|---|---|---|
| `valid` | 可用 | —— |
| `billing_blocked` | 402 —— 停用，额度耗尽 | 充值，然后 Validate（成功后自动恢复为 `valid`） |
| `needs_reauth` | 401/403 —— 停用，凭证被拒 | 重新添加 key / 登录 |
| `revoked` | 永久失效 | 更换 |
| `rate_limited` | 429 —— 短暂限流 | 下次成功 / 窗口到期后自动恢复 |

不再有独立的 COOLING 标记：status 列本身已说明一切。窗口已过期的
`rate_limited` 上报为 `valid`。

**内部调度（永不展示）：**

- 429 会保留一个短的 `cooldown_until_ms`，让多 key 轮换跳过被限流的
  key；单 key 配置仍然照常发送（聊胜于无）。
- 5xx / 网络错误不会触碰凭证 —— 传输层失败跟 key 健康度无关
  （OpenClaw 语义）。
- 请求级 4xx（404/400/422）不会触碰凭证（已较早作为 `request_error`
  落地）。

**聊天侧：** 流式错误已经以红色错误气泡呈现，并带有 provider 自己的消息
（“Insufficient Balance”，……）—— 这就是面向用户的通知；账号面板仅用于
诊断。

## 改动

1. `auth/usage.py report_failure` —— 只有 `rate_limit`、`rate_limit_long`、
   `billing_blocked`、`needs_reauth` 会到达账号池；`request_error`、
   `server_error`、`network_error` 直接返回，不触碰它。
2. `auth/pool.py mark_failure` —— `billing_blocked` 设置状态但不带冷却
   时间戳（停用直到重新验证，而非“等 24 小时”）。
3. `auth/pool.py` 自动恢复 —— 只有 `rate_limited` 会自愈；
   `billing_blocked` 被排除（validate 是唯一的恢复途径）。
4. `auth/usage.py _account_healthy` —— `billing_blocked` 与
   `revoked`/`needs_reauth` 一同被视为对轮换不健康。
5. `webui/routes/accounts.py` —— Validate 成功后写入
   `status="valid"`，清除 cooldown + last_error（闭合 充值 →
   Validate → 恢复 的循环）；account 记录中移除 `cooling` 字段；
   超过窗口的 `rate_limited` 上报为 `valid`。
6. `web .. account-manager.tsx` —— 移除 COOLING 标记；status 渲染为
   有效 / 限流中 / 欠费停用 / 需重新验证 / 已失效。
