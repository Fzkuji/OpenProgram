# LLM 调用容错与超时管理

一项跨项目研究，考察参考用的 agent 框架如何稳健地调用 LLM——重试、退避、超时、连接处理、故障转移——以及 OpenProgram 在 2026-05 加固阶段之后所处的位置。

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

| 维度 | openclaw | opencode | hermes-agent | pi-ai (codex) | OpenProgram（当前） |
|---|---|---|---|---|---|
| 重试次数 | 3（+2 次内层瞬时） | 2 | 3 | 3 | 3 |
| 退避基数 | 300 ms | 500 ms | 5 s | 1 s | 1 s |
| 退避上限 | 30 s | 10 s | 120 s | 无 | **30 s** ✅ 新增 |
| 抖动 | 对称 / 仅正向 | ±20% | 去相关（0.5） | 无 | 对称 / 仅正向 |
| 可重试状态码 | 408/409/429/5xx | 429/503/504/529 | 429/5xx/524 | 429/5xx | 429/5xx + 响应体模式 |
| Retry-After | ms+秒+日期 | ms+秒+日期（上限 10s） | 无 | 无 | **ms+秒+日期** ✅ 新增 |
| 响应体 / 空闲超时 | **30 分钟，任意字节** (undici) | 无（HTTP）/ 5 分钟（WS） | 180 s 失活，按上下文缩放 | 无 | **30 分钟任意字节 + 15 分钟数据停滞 + 2 小时上限** ✅ 新增 |
| 连接超时 | undici 默认 | 无 / 15 s（WS） | SDK 默认 | 无 | 30 s |
| TTFB 守卫 | 30 s（Azure） | 不适用 | 120 s（codex） | 无 | 由空闲/读取超时覆盖 |
| HTTP 版本 | **强制 HTTP/1.1** | 默认 | 自动（h2） | — | httpx 默认（h1.1） |
| IPv6 / Happy Eyeballs | **autoSelectFamily** | 否 | 否 | — | ❌ 缺口 |
| TCP keepalive 调优 | undici 默认 | 否 | **SO_KEEPALIVE 30/10/3** | — | ❌ 缺口 |
| 连接复用 | undici keep-alive | WS 连接池，55 分钟回收 | **共享 client + 失活时重建** | — | ❌ 每次调用新建 client |
| API-key 轮换 | **是** | 否 | **是（连接池 + 冷却）** | — | ❌ 缺口 |
| Provider/模型故障转移 | **是** | 仅 WS→HTTP | **是（链式）** | — | ❌ 缺口 |
| 首 token 之后中断 | 报错 | 报错 | **部分结果 + 续接** | 报错 | 报错 |
| 调用中途刷新 OAuth | — | — | **逐请求 token provider** | 逐次调用 | 逐次调用解析 |
| 限流 header 解析 | — | **是（x-ratelimit-*）** | 是（Nous） | — | ❌ 缺口 |
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
- `MAX_RETRIES=3`、`BASE_DELAY_MS=1000`，重试 429/5xx + 响应体模式——
  **没有显式的响应体读取超时**；依赖 fetch + 重试。（我们旧的
  120s httpx 读取上限是 OpenProgram 独有的添加——也就是那个 bug。）

---

## 3. 我们的改动（2026-05 阶段）

全部在 `openprogram/providers/`：

1. **Codex 超时解耦并放宽** (`openai_codex/openai_codex.py`)：
   - httpx `Timeout(connect=30, read=1860, write=30, pool=30)`——旧的
     单一 `timeout=120` 浮点值把响应体读取上限卡在 120 s，
     在带缓冲的代理/VPN 上会先于我们的空闲预算触发（即报告的那个 bug）。
   - SSE 调控器重建为**两个预算 + 一个兜底**，对齐
     openclaw 的“宽松、任意字节即重置”模型：
     - `SSE_IDLE_TIMEOUT_S = 1800`（30 分钟）——“完全没有字节”，在
       **任意**行（包括 ping）时重置 ≈ openclaw 的 `bodyTimeout`。
     - `SSE_DATA_STALL_TIMEOUT_S = 900`（15 分钟）——**我们额外增加的**：“没有真正
       数据”，仅在解析到事件时重置；捕获 openclaw 看不到的 ping 洪泛停滞。
     - `SSE_TOTAL_TIMEOUT_S = 7200`（2 小时）——失控兜底。
   - 全部可通过环境变量覆盖（`OPENPROGRAM_SSE_*`、`OPENPROGRAM_HTTPX_*`）。

2. **退避上限** (`utils/stream_retry.py`)：指数部分上限封顶为
   30 s (`OPENPROGRAM_PROVIDER_STREAM_BACKOFF_MAX_S`)；更大的
   服务器 Retry-After 仍会被遵循。

3. **Retry-After：三种形式全支持** (`utils/errors.py`)：`retry-after-ms`、
   整数秒，以及 HTTP-date——之前仅支持秒。

---

## 4. 现已实现 vs 推迟

**已实现（`providers/utils/` 下的新模块，已接入 codex；其中
通用模块对每个 HTTP provider 都可用）：**

- **集中式超时策略** (`timeouts.py`)——单一事实来源，放宽
  到 OpenClaw 的 30 分钟级别，带上下文缩放辅助方法。
- **稳健的 client 构建器** (`http_client.py`)：
  - **TCP keepalive**——`SO_KEEPALIVE` + 空闲/间隔/次数 → 约 60 s 死对端
    检测（VPN 掉线场景）。按操作系统做防御性处理；`OPENPROGRAM_TCP_KEEPALIVE=0`
    可禁用。
  - **强制 IPv4** 逃生口 (`OPENPROGRAM_FORCE_IPV4=1`)，用于损坏的 IPv6 VPN
    （绑定一个 IPv4 源地址——httpx 没有 Happy-Eyeballs）。
  - **连接复用**——`get_shared_async_client`（按事件循环作键）；codex 现在
    跨回合复用其 TLS 连接，而不是重新握手。
  - 通过 httpx 0.28 的 `proxy=` 实现 **代理**（修复了已移除的 `proxies=` 形式，
    一个潜伏的崩溃）。
- **限流 header 解析** (`rate_limit.py`)——`x-ratelimit-*` /
  `anthropic-ratelimit-*`；codex 在某个配额桶偏低/耗尽时发出告警。
- **部分响应恢复** (`openai_codex.py`)——在内容产生*之后*发生的瞬时流中
  断会以 (`stop_reason="length"`) 终结这个部分回合，
  而不是报错；永久性失败（auth/invalid/context/policy）仍然
  硬失败。开关 `OPENPROGRAM_PARTIAL_RECOVERY=0`。
- **Provider/模型故障转移** (`failover.py` + `agent_loop.py`)——分类器
  （rate_limit/overloaded/server/timeout/network）+ 一个 `stream_with_failover`
  包装器，在**内容产生之前**遇到值得故障转移的失败时，先尝试主选项再依次尝试
  每个配置的回退项（转发事件、抑制重复的 `start`、token 已流出后绝不切换）。接入
  回合循环时**默认关闭**：除非设置了 `OPENPROGRAM_FALLBACK_MODELS`
  ("provider/model,provider2/model2")，否则为空操作。
- 将 codex + gemini_cli 接入共享 client；**修复了 gemini 的
  `timeout=120.0` 单浮点 bug**（与 codex 同类）。
- （更早）退避**上限** + **Retry-After** 三种形式全支持。

**在不适用之处干净地禁用（已设计，默认关闭）：**

- **API-key 轮换**——完整机制已存在于 auth 层
  (`auth/pool.py`：`pick` 轮换 + `mark_failure`/`report_failure` 冷却，
  带策略与 TTL)。当一个池有 >1 个凭证时，**获取时**的轮换是自动的。
  逐次调用的**失败冷却**上报被刻意
  不接入实时单账户路径：把唯一的凭证拉进冷却
  会把用户自我锁死而毫无收益。所以在单账户上，轮换
  是干净的空操作；一旦配置了多个凭证它便自动
  激活。热路径里没有半成品的危险代码。
- **OAuth 逐请求 token provider**——codex 已经通过 auth manager 在每次调用时解
  析 + 刷新 bearer；完整的 httpx 事件钩子 provider 只是
  锦上添花，不是修复，因此略去。

---

## 5. 新增的可调项

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
| `OPENPROGRAM_FALLBACK_MODELS` | （空） | `provider/model,…`——启用 provider/模型故障转移 |
