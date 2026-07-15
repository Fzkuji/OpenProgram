# fast tier

部分厂商给部分模型提供付费高速档。OpenProgram 把它做成聊天界面的"高速"开关：打开后请求按厂商协议带上高速档参数。

## 哪些模型有

只有两个家族存在 fast 档：

| 家族 | 请求形态 |
|---|---|
| GPT 5.4 / 5.5 / 5.6 系（OpenAI priority processing） | 请求体 `service_tier: "priority"` |
| Claude Opus 4.6 / 4.7 / 4.8 | 请求体 `speed: "fast"` + fast-mode beta 头 |

其他模型（Gemini、DeepSeek、Qwen 等）没有 fast 概念，开关不会出现。

判定按模型，看运行时注册表里的 `Model.fast` 字段。配置里模型行显式写了 `fast` 就听它的——`openai-codex` 的值来自官方模型端点的 `service_tiers` 数据（登录订阅后 Fetch 即更新，不靠手写清单）；没写的按内置声明回填，声明只覆盖上述两个家族，按模型 id 匹配、与 provider 无关——同一个模型经网关转售照样保留高速档。

## 怎么开

- 聊天输入框的"高速"菜单项 / chip：对当前模型逐次生效，切到不支持的模型后开关自动隐藏、参数不会发出。
- agent 配置里的 `service_tier`：给某个 agent 存一个默认档，每轮请求可再覆盖。

## 对哪些 provider 生效

请求构建侧只有这些线路透传高速参数：`openai_responses`、`openai_completions`、`openai_codex`（请求体 `service_tier`），以及 `anthropic`（仅当模型声明了 fast 时切换到 `speed: "fast"` + beta 头）。其他 provider 不透传，参数不出网。

计费提醒：fast 档按量计费。Claude 订阅账户没有充值 usage credits 时，Anthropic 会返回 429 "Usage credits are required for fast mode"——这是账户问题，不代表模型不支持，界面会原样展示该报错。

实现细节与判定规则的完整记录见[设计笔记](../reference/design/providers/models/fast-tier.md)。
