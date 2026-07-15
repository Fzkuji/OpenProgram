# 请求构建:Context → 各 provider 参数

Status: **decided** · Created: 2026-06-24

> providers 层的唯一职责:**拿到一个准备好的 `Context`,翻译成当前 provider 的 wire
> 请求,并按该 provider 的机制落地 prompt 缓存。**
>
> providers 不关心 `Context` 是怎么构建出来的 —— 系统提示里身份/工具/记忆怎么拼、
> 要不要分段,那是上游 [`../context/`](../context/) 的事。本层只"收什么、翻译什么、
> 发什么"。

---

## 一、核心:一份统一格式 + 每 provider 一个翻译

上游产出一个 provider 无关的 `Context`;本层按 `model.api` 分发到对应 provider 的
翻译,转成那家认的请求。provider 差异全收在翻译层,上游永远只跟 `Context` 打交道。

```
Context(上游已构建好)
    │  按 model.api 分发
    ▼
该 provider 的翻译:Context → 它的 wire 请求
```

业界共识做法(opencode / hermes / openclaw 均如此)。

## 二、统一格式:Context

```
Context {
  system_prompt    系统提示(本层视作已准备好的内容,不问怎么来的)
  messages         对话(user / assistant / tool_result)
  tools            工具清单
}
```

三者分开存,system 独立。本框架的 `Context` 以 system 独立为基准 —— 翻译给
system 独立的 API(Anthropic / Bedrock / Gemini)直接对应;翻译给 OpenAI 一类时
由该 provider 的翻译把 system 落进 messages。

## 三、翻译层:system 落点 + 字段映射

每 provider 一段翻译(`_build_system` / `_build_messages` / `_build_tools`),按
`model.api` 分发。核心差异在 system 的落点和字段名:

| provider 风格 | system 落到哪 | 对话字段 | 工具字段 |
|---|---|---|---|
| anthropic-messages | 独立 `system` | `messages` | `tools`(strict 在 tool 对象内) |
| openai-completions | system/developer 消息进 `messages[0]` | `messages` | `tools`(strict 是 boolean) |
| openai-responses / codex | 抽成 `instructions` 参数 | `input` | `tools` |
| google (gemini) | `systemInstruction`;assistant→model | `contents`(内容叫 parts) | `tools` |
| bedrock | 独立 system | `messages` | `tools` |

翻译还抹平各家特有的块:思考块签名、tool_use 参数是对象还是字符串、工具 schema
方言(strict / additionalProperties)。这部分已实现。

## 四、缓存:三种 mode + 声明层

各家缓存机制根本不同,**不用一个抽象硬覆盖**,分三种 mode,每 provider 声明自己属哪种。

| mode | 谁 | 做法 |
|---|---|---|
| `explicit` | Anthropic, Bedrock | 请求里显式打缓存断点(cache_control / cachePoint) |
| `auto` | OpenAI 系 | 不打断点,自动前缀缓存;可传缓存键(prompt_cache_key) |
| `none` | 无缓存的兼容 provider | 什么都不做 |
| `out_of_band` | Gemini | 先调独立 API 存缓存对象拿 ID,下次带 ID(两步;暂仅读命中统计) |

### cache_spec 声明层

照搬 `models/` 下 thinking 的声明式范式:每 provider 一份 `cache.json` 声明
`mode` + 缓存键参数名 + TTL 映射 + 断点上限。公共模块 `cache_spec.py` 加载它
(`get_cache_spec` / `cache_mode` / `ttl_for_retention` / `cache_key_param`),
provider 代码读声明决定行为,而不是把规则硬编码在 `stream_simple` 里。无声明的
provider 走 `none` 兜底(和 thinking 的 OpenAI 兼容兜底同理)。

```json
{
  "mode": "explicit",
  "breakpoint_format": "cache_control",        // 或 "cachePoint" (bedrock)
  "retention_ttl_map": {"short": null, "long": "1h"},
  "max_breakpoints": 4
}
```

```json
{ "mode": "auto", "cache_key_param": "prompt_cache_key" }
```

```json
{ "mode": "none" }
```

### 断点打在哪(explicit 模式)

调用方可在某个 content block 上显式标 `cache_control`,原样透传到该 block 之后
(见 [`../../plans/cache-control-passthrough.md`](../plans/cache-control-passthrough.md));
未标时 provider 自动在最后一块打。断点上限(Anthropic 4 个)由 cache_spec 的
`max_breakpoints` 约束,超了按 `tools > system > messages` 优先级丢低的。

> 缓存断点位置如需按"上游标的稳定段"统一决定,靠的是 `TextContent.cache_control`
> 这个已有的逐块标记字段 —— 上游在稳定段的 block 上标,本层透传。**不引入新的
> Context 级结构,也不要求改 system_prompt 类型。**

## 五、与上游的接口

```
../context/                     providers(本层)
构建 Context、决定内容、    ──→   翻译成各家 wire、缓存按 mode 落地
在 block 上标 cache_control   Context     读 cache_spec、透传缓存标记
```

契约 = `Context`(content block 可带 `cache_control`)。上游怎么构建上下文与本层
完全解耦:加 provider 只动本层 + 一份 `cache.json`,改上下文构建只动 `../context/`。

## 六、实现(port 自 opencode)

缓存策略层照搬 opencode 的 `cache-policy.ts` + `protocols/utils/cache.ts`
(`references/opencode/packages/llm/src/`),用 Python 复刻。落地清单:

| 件 | 文件 | 抄自 opencode 的什么 |
|---|---|---|
| 声明加载 | `providers/cache_spec.py` + 各 provider `cache.json` | `RESPECTS_INLINE_HINTS` 那套"按 provider 声明缓存能力" |
| 自动断点策略 | `providers/cache_policy.py` 的 `apply_cache_policy` | `applyCachePolicy`:标最后一个 tool + 最近 user 消息,不覆盖调用方手动标记 |
| 断点预算 | `cache_policy.py` 内 `_take`/`max_breakpoints` | `Breakpoints{remaining,dropped}` + 4 断点上限 |
| TTL 分桶 | `cache_policy.py` 的 `_ttl_bucket` | `ttlBucket`(≥3600s → "1h",否则默认 5m) |
| tool 级断点 | `Tool.cache_control` 字段 + anthropic `_build_tools` 透传 | opencode 给 tool 也标 cache 的能力 |

接入点:anthropic `stream_simple` 在构建 messages/tools 前调 `apply_cache_policy`;
`_get_cache_control` 改读 `cache.json` 的 ttl 映射与 `long_ttl_endpoints`,不再硬编码。

bedrock 也声明为 explicit,但它用 `cachePoint`(独立块)而非挂在 block 上的
`cache_control`,且已自带"在最后一条消息打断点"的逻辑;把它也纳入统一的
`apply_cache_policy`(tool 断点)是后续增量,本次未接 —— 现状能用。

与 opencode 的一处差异:opencode 的 `system` 是分段数组,能在"最后一段 system"上
单独标断点;本层 `Context.system_prompt` 是单字符串,system 断点由各 provider 的
`_build_system` 在那一整块上打。policy 层只覆盖 tools + messages。
