# Token 计量

各厂商上报 token 用量的字段名和口径都不一样（有的把缓存 token 算进输入总数，有的不算）。provider 层把它们统一归一化成每条 assistant 消息一份用量记录，界面和会话记录不接触厂商格式。

## 统一格式

每条 assistant 消息带一个 `Usage` 记录（定义见 [`openprogram/providers/types.py`](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/providers/types.py)）：

| 字段 | 含义 |
|---|---|
| `input` | 非缓存输入 token（厂商上报的是含缓存总数时，减去缓存部分） |
| `output` | 输出 token |
| `cache_read` | 从 prompt 缓存命中的输入 token |
| `cache_write` | 写入 prompt 缓存的输入 token（仅显式缓存的 provider） |
| `total_tokens` | 厂商上报的本次调用总数 |
| `cost` | 美元成本，按启用模型的每百万 token 单价计算（input / output / cache read / cache write 四项） |

口径沿用 Anthropic 约定：`input` 不含缓存 token。输入计数含缓存的厂商（OpenAI 系协议）通过减去缓存数换算。

## 各 provider 上报什么

各流式实现从厂商的最终事件或最后一个 chunk 里取用量：

| 协议（provider） | 原始字段 | 缓存统计 |
|---|---|---|
| Anthropic Messages（`anthropic`、`claude-code`，以及 `minimax`、`kimi_coding`、`vercel_ai_gateway` 等 Anthropic 协议网关） | `input_tokens`、`output_tokens`、`cache_read_input_tokens`、`cache_creation_input_tokens` | 读 + 写 |
| OpenAI Responses（`openai`、`openai_codex`、`azure_openai_responses`、`github_copilot`） | `input_tokens`（含缓存——已减去）、`output_tokens`、`input_tokens_details.cached_tokens`、`total_tokens` | 仅读 |
| OpenAI Completions（`deepseek`、`groq`、`mistral`、`openrouter` 等兼容端点） | `prompt_tokens`、`completion_tokens`（`completion_tokens_details` 里的推理 token 从输出计数中拆出）、`total_tokens` | 无 |
| Google Generative AI（`google`） | `prompt_token_count`、`candidates_token_count`、`total_token_count` | 无 |
| Cloud Code Assist（`gemini_subscription`、`google_gemini_cli`） | `promptTokenCount`、`candidatesTokenCount` + `thoughtsTokenCount`、`cachedContentTokenCount`、`totalTokenCount` | 仅读 |
| Bedrock Converse Stream（`amazon_bedrock`） | `inputTokens`、`outputTokens`、`cacheReadInputTokens`、`cacheWriteInputTokens`、`totalTokens` | 读 + 写 |

成本在取到用量后立即按模型行的价格计算。OpenAI Responses 协议上，响应实际返回的 `service_tier` 会再调整单价，[fast tier](fast-tier.md) 请求按 priority 档价格计费。

## 用量显示在哪

- **按消息**：每条 assistant 消息存自己的用量和成本，随会话持久化。
- **聊天角标**：输入框旁的角标显示最近一次调用的用量（`11.2k in · 450 out`）。不跨调用累加——最近一次调用的输入 token 数就是当前上下文占用量，这才是有意义的数字。悬浮提示拆分输入：Claude 协议 provider 分 base / cache write / cache hit，Codex 分 base / cached，其余显示缓存命中百分比。数字超过一千显示为 `1.2k`，超过一百万显示为 `1.0m`。
- **上下文条**：每轮结束后服务端通过聊天 WebSocket 推送 `context_stats` 事件，带聊天用量和模型上下文窗口，界面据此渲染占用百分比。
- **函数执行**：每次函数运行用独立 runtime，用量按次显示在函数卡片上，与聊天对话的数字互不影响。

provider 按次上报用量，所以任何地方都没有按 provider 区分的累加逻辑：最新值永远描述最近一次请求。
