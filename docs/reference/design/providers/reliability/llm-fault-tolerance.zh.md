# LLM 调用容错与超时管理

OpenProgram 如何稳健地调用 LLM —— 重试、退避、超时、连接处理、故障转移 —— 以及参考用的 agent 框架如何解决同样的问题。

研究的来源（全部位于 `references/` 下，只读）：

| 项目 | 语言 | 角色 |
|---|---|---|
| **openclaw** | TS | Claude-Code 风格的 agent；传输层最完整 |
| **opencode** (sst/opencode) | TS | Effect.js + Vercel-AI-SDK 风格的执行器 |
| **hermes-agent** (NousResearch) | **Py** | 与我们最为相似；容错能力最丰富 |
| **pi-ai** (badlogic/pi-mono) | TS | 我们的 codex provider 直接移植自此参考 |
| **claude-code** | TS | 部分打包；HTTP 行为 = Anthropic SDK |

---

## 1. 对比矩阵

| 维度 | openclaw | opencode | hermes-agent | pi-ai (codex) | OpenProgram |
|---|---|---|---|---|---|
| 重试次数 | 3（+2 次内层瞬时） | 2 | 3 | 3 | 3 |
| 退避基数 | 300 ms | 500 ms | 5 s | 1 s | 1 s |
| 退避上限 | 30 s | 10 s | 120 s | 无 | **30 s** |
| 抖动 | 对称 / 仅正向 | ±20% | 去相关（0.5） | 无 | 对称 / 仅正向 |
| 可重试状态码 | 408/409/429/5xx | 429/503/504/529 | 429/5xx/524 | 429/5xx | 429/5xx + 响应体模式 |
| Retry-After | ms+秒+日期 | ms+秒+日期（上限 10s） | 无 | 无 | **ms+秒+日期** |
| 响应体 / 空闲超时 | **30 分钟，任意字节** (undici) | 无（HTTP）/ 5 分钟（WS） | 180 s 失活，按上下文缩放 | 无 | **30 分钟任意字节 + 15 分钟数据停滞 + 2 小时上限** |
| 连接超时 | undici 默认 | 无 / 15 s（WS） | SDK 默认 | 无 | 30 s |
| TTFB 守卫 | 30 s（Azure） | 不适用 | 120 s（codex） | 无 | 由空闲/读取超时覆盖 |
| HTTP 版本 | **强制 HTTP/1.1** | 默认 | 自动（h2） | — | httpx 默认（h1.1） |
| IPv6 / Happy Eyeballs | **autoSelectFamily** | 否 | 否 | — | 强制 IPv4 的退路 |
| TCP keepalive 调优 | undici 默认 | 否 | **SO_KEEPALIVE 30/10/3** | — | **SO_KEEPALIVE 30/10/3** |
| 连接复用 | undici keep-alive | WS 连接池，55 分钟回收 | **共享 client + 失活时重建** | — | 按事件循环共享 client |
| API-key 轮换 | **是** | 否 | **是（连接池 + 冷却）** | — | 是（连接池 + 冷却） |
| Provider/模型故障转移 | **是** | 仅 WS→HTTP | **是（链式）** | — | **是（链式，默认开启）** |
| 首 token 之后中断 | 报错 | 报错 | **部分结果 + 续接** | 报错 | 部分结果 + 续接 |
| 调用中途刷新 OAuth | — | — | **逐请求 token provider** | 逐次调用 | 逐次调用解析 |
| 限流 header 解析 | — | **是（x-ratelimit-*）** | 是（Nous） | — | 是（x-ratelimit-* / anthropic-ratelimit-*） |
| 错误分类 | 是 | 是（带标签联合类型） | 是 | 基础 | 是（`ErrorReason`） |

---

## 2. 各项目值得注意的模式

### openclaw（传输层最佳）
- **流超时 = 30 分钟，设置在 undici 全局 dispatcher 上**，形式为
  `bodyTimeout = headersTimeout = DEFAULT_UNDICI_STREAM_TIMEOUT_MS`
  (`src/infra/net/undici-global-dispatcher.ts:16`)，在收到**任意**字节时重置。
  *这是关键洞见：*不要给推理流设一个很紧的读取超时——给它 30 分钟，任何流量都重置。
- **强制 HTTP/1.1** (`allowH2:false`) 与 **Happy Eyeballs**
  (`autoSelectFamily`)——避免 h2 流重置以及损坏的 IPv6 挂起
  （经典的 VPN 故障）。
- **两层重试**：外层 `retry.ts`（3 次，300ms→30s）+ 内层
  `operation-retry.ts`（2 次，250ms→1s），用于瞬时的 provider 操作。
- **API-key 轮换** (`api-key-rotation.ts`)：外层循环遍历 key，
  每个 key 内层做瞬时重试。
- **故障转移分类** (`failover-matches.ts`)：rate_limit / overloaded
  / server / timeout / network——各为一个正则分组。
- 在遵循 Retry-After 时使用**仅正向抖动**（睡眠绝不少于服务器要求的时长）；
  通过 `x-should-retry` 实现 **SDK 重试旁路**。

### hermes-agent（最丰富；Python，与我们最接近）
- **TCP keepalive socket 注入** (`run_agent.py`)：`SO_KEEPALIVE=1`、
  `TCP_KEEPIDLE=30s`、`TCP_KEEPINTVL=10s`、`TCP_KEEPCNT=3` → **约 60 s 内检测到死对端**
  而不是一直挂起。另外在 SDK 关闭之前强制关闭 TCP，以避免 CLOSE_WAIT 堆积。
- **去相关抖动退避**，种子取自 `time_ns ^ counter`，使得
  并发会话不会步调一致地重试（基数 5s，×2，上限 120s）。
- **按上下文缩放的流失活超时**：基准 180s，>50k tokens →240s，
  >100k tokens →300s；本地 provider 完全禁用。
- **TTFB 与事件间超时分开** （codex TTFB 120s，在
  超过 25k 上下文时禁用，以避免长 prefill 期间的误报）。
- **凭证池**，带轮换策略（round-robin / least-used）
  以及耗尽冷却（401→5 分钟，429/402→1 小时，dead→24 小时后剔除）。
- 通过 httpx 事件钩子实现 **OAuth 逐请求 token provider**（刷新
  偏移 60 s）——token 在会话中途刷新，无需重建 client。
- **部分响应恢复**：在首 token *之后*发生中断时，它
  返回部分文本 + `finish_reason=length`，并让下一
  回合续接——不丢失工作成果，也不盲目重试。

### opencode
- **HTTP 上无响应体/空闲超时**——流无界限（与 pi-ai 相同）。
- WebSocket 路径：连接 15s，空闲 5min（每帧重置），**55 分钟
  按连接寿命回收**，5 次流失败后回退 WS→HTTP。
- 为 OpenAI + Anthropic 做**限流 header 解析**，解析为一个结构化
  对象（支持主动的客户端节流）。
- 带标签联合类型的错误模型；遵循 Retry-After（上限 10s）。

### pi-ai（我们 codex 的参考）
- `MAX_RETRIES=3`、`BASE_DELAY_MS=1000`，重试 429/5xx + 响应体模式 ——
  **没有显式的响应体读取超时**；依赖 fetch + 重试。这里没有 120 s httpx 读取
  上限的对应物，加上这样一个上限正是长流被误杀的原因（见 §3）。

---

## 3. 超时策略

指导原则来自 openclaw：推理流不能带一个紧的读取超时。单一的 httpx
`timeout=120` 浮点值把响应体读取上限卡在 120 s，在带缓冲的代理或 VPN 上会先于
空闲预算触发 —— 一个健康的长流就是这样被杀掉的。

因此 codex 使用 `Timeout(connect=30, read=1860, write=30, pool=30)`，
SSE 调控器是两个预算加一个兜底：

- `SSE_IDLE_TIMEOUT_S = 1800`（30 分钟）—— "完全没有字节"，在**任意**行
  （包括 ping）时重置，相当于 openclaw 的 `bodyTimeout`。
- `SSE_DATA_STALL_TIMEOUT_S = 900`（15 分钟）—— "没有真正数据"，仅在解析到事件时
  重置。它能捕获字节级超时看不到的 ping 洪泛停滞。
- `SSE_TOTAL_TIMEOUT_S = 7200`（2 小时）—— 失控兜底。

三者都可通过环境变量覆盖（`OPENPROGRAM_SSE_*`、`OPENPROGRAM_HTTPX_*`）。

退避的指数部分上限封顶为 30 s（`OPENPROGRAM_PROVIDER_STREAM_BACKOFF_MAX_S`，
位于 `utils/stream_retry.py`）；更大的服务器 Retry-After 仍会被遵循。
`utils/errors.py` 解析 Retry-After 的三种形式：`retry-after-ms`、整数秒，
以及 HTTP-date。

## 4. 传输与恢复

`providers/utils/` 下的模块是通用的，对每个 HTTP provider 都可用；codex 全部接入。
openai-completions 在产出内容之前，对 `classify_error` 判定可重试的 `APIError`
用同一模型、`PROVIDER_STREAM_MAX_ATTEMPTS` 与 `stream_backoff_seconds` 重试；
一旦已经流出内容则直接上抛。

- **集中式超时策略**（`timeouts.py`）—— 单一事实来源，处在 OpenClaw 的 30 分钟
  级别，带上下文缩放辅助方法。
- **client 构建器**（`http_client.py`）：
  - **TCP keepalive** —— `SO_KEEPALIVE` 加空闲/间隔/次数，给出约 60 s 的死对端
    检测，对应 VPN 掉线场景。按操作系统做防御性处理；
    `OPENPROGRAM_TCP_KEEPALIVE=0` 可关闭。
  - **强制 IPv4** 退路（`OPENPROGRAM_FORCE_IPV4=1`）用于 IPv6 损坏的 VPN，
    做法是绑定一个 IPv4 源地址，因为 httpx 没有 Happy Eyeballs。
  - **连接复用** —— `get_shared_async_client` 按事件循环取键，因此 codex 跨轮次
    复用它的 TLS 连接，而不是重新握手。
  - **代理**经由 httpx 0.28 的 `proxy=`。
- **限流 header 解析**（`rate_limit.py`）—— `x-ratelimit-*` 与
  `anthropic-ratelimit-*`；codex 在某个额度桶偏低或耗尽时发出警告。
- **部分结果恢复**（`openai_codex.py`）—— 在内容已经到达之后发生的瞬时中断，
  会以 `stop_reason="length"` 结束这一轮的部分结果，而不是报错，因此不丢工作、
  也不会盲目重试。永久性失败（鉴权、非法请求、上下文、策略）仍然直接失败。
  可用 `OPENPROGRAM_PARTIAL_RECOVERY=0` 关闭。
- **Provider/模型故障转移**（`failover.py` + `agent_loop.py`）—— 一个分类器
  （rate_limit / overloaded / server / timeout / network）加一个
  `stream_with_failover` 包装器：在**内容产生之前**发生值得转移的失败时，
  依次尝试主模型和每个候选。它转发事件、抑制重复的 `start`，
  并且在已经流出 token 之后绝不切换。**默认开启，且保守：**什么都不配置时，
  候选链就是用户在**同一 provider** 下启用的其他模型（最多 2 个，按配置行顺序），
  因此故障转移复用本来就要用的那份凭据，绝不会去联系用户没有配置过的 provider。
  设置 `OPENPROGRAM_FALLBACK_MODELS="provider/model,provider2/model2"` 可用显式
  名单覆盖它，显式名单允许跨 provider；设成 `off`（或 `none`）则完全关闭故障转移。
- **openai-completions 内容前重试**（`openai_completions.py`）—— `EventStart`
  之后、任何 content block 之前，可重试的 `APIError`（包括 xAI 无 HTTP status 的
  `Internal error during token generation`）会在同一模型上重新打开流。内容已经
  到达、或错误不可重试时，外层原有的 `APIError` 处理记录凭据冷却并重新抛出。
- gemini_cli 共用同一个 client，因此具有相同的超时语义，而不是自带一个单一浮点
  超时值。

**有意关闭或未构建的部分：**

- **API-key 轮换** —— 机制位于 auth 层（`auth/pool.py`：`pick` 轮换、
  `mark_failure` / `record_call_failure` 冷却，带策略与 TTL）。当一个池里有多于一份
  凭据时，获取时的轮换是自动的。逐次调用的失败冷却上报没有接入单账号的实时路径，
  因为让唯一一份凭据进入冷却只会把用户锁在门外而没有任何收益。单账号下轮换是
  干净的 no-op，一旦配置了多份凭据便自行生效。
- **逐请求 OAuth token provider** —— codex 已经在每次调用时经 auth manager 解析
  并刷新 bearer，因此完整的 httpx event-hook provider 只会增加机制而不解决问题。

## 5. 可调项

| 环境变量 | 默认值 | 含义 |
|---|---|---|
| `OPENPROGRAM_SSE_IDLE_TIMEOUT_S` | 1800 | 完全没有字节（任意行即重置） |
| `OPENPROGRAM_SSE_DATA_STALL_TIMEOUT_S` | 900 | 没有真正数据（数据即重置） |
| `OPENPROGRAM_SSE_TOTAL_TIMEOUT_S` | 7200 | 单条流的失控上限 |
| `OPENPROGRAM_HTTPX_CONNECT_TIMEOUT_S` | 30 | 连接（快速失败掉线的 VPN） |
| `OPENPROGRAM_HTTPX_READ_TIMEOUT_S` | idle+60 | httpx 读取兜底 |
| `OPENPROGRAM_PROVIDER_STREAM_RETRIES` | 3 | 每条流的重试次数 |
| `OPENPROGRAM_PROVIDER_STREAM_BACKOFF_S` | 1.0 | 退避基数 |
| `OPENPROGRAM_PROVIDER_STREAM_BACKOFF_MAX_S` | 30.0 | 退避指数上限 |
| `OPENPROGRAM_TCP_KEEPALIVE` | 1 | 启用 TCP keepalive（死对端检测） |
| `OPENPROGRAM_TCP_KEEPIDLE_S` / `_KEEPINTVL_S` / `_KEEPCNT` | 30 / 10 / 3 | keepalive 探测时序（约 60 s 检测） |
| `OPENPROGRAM_FORCE_IPV4` | 0 | 绑定 IPv4 源地址（损坏的 IPv6 VPN） |
| `OPENPROGRAM_PARTIAL_RECOVERY` | 1 | 在流中途中断时抢救部分输出 |
| `OPENPROGRAM_FALLBACK_MODELS` | （空）＝同 provider 候选链 | `provider/model,…`——显式故障转移链，允许跨 provider；`off`/`none` 关闭 |
