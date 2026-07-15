# Provider 一览

本页列出仓库内置的 provider 实现（`openprogram/providers/` 下每个子目录一个）、各自的接入方式，以及在 Python 代码里直接使用 provider 的方法。内置实现之外，`openprogram providers available` 还能列出几百个走 OpenAI 兼容协议的社区 provider 目录，配置方式相同。

## 内置 provider

接入方式说明：**API key** = 存入凭据库的密钥（`providers login <id>`，也可从环境变量导入）；**OAuth** = 浏览器 / 设备码登录订阅账号；**CLI 凭据** = 直接读外部 CLI 已登录的凭据文件；**云凭据链** = 运行时自动走云厂商的标准凭据链。

| Provider | 协议 | 接入方式 | 备注 |
|---|---|---|---|
| `anthropic` | Anthropic Messages | API key（`ANTHROPIC_API_KEY`）或 OAuth（Claude 订阅，PKCE / `claude setup-token` 粘贴） | 显式 prompt caching（`cache_control`，支持 1h TTL） |
| `openai` | OpenAI Responses | API key（`OPENAI_API_KEY`） | Responses 协议自动缓存（`prompt_cache_key`） |
| `openai_responses` / `openai_completions` | OpenAI Responses / Chat Completions | —（共享协议实现，被众多 provider 复用） | |
| `openai_codex` | ChatGPT 后端 | OAuth（ChatGPT 订阅）：浏览器 PKCE 登录；已有的 `codex` CLI 登录态也可用 `providers discover` 导入 | 模型清单从官方端点实时拉取 |
| `azure_openai_responses` | Azure OpenAI Responses | API key（`AZURE_OPENAI_API_KEY`）+ 自填 base URL | |
| `google` | Google Generative AI | API key（`GEMINI_API_KEY` / `GOOGLE_API_KEY`） | thinking 用 token budget 控制 |
| `google_gemini_cli` | Cloud Code Assist | CLI 凭据：直接读 `~/.gemini/oauth_creds.json`，刷新由 Gemini CLI 负责 | |
| `gemini_subscription` | Cloud Code Assist | CLI 凭据：导入 `~/.gemini/oauth_creds.json`（先用 Gemini CLI 登录） | 别名 `gemini`、`gemini-cli` |
| `amazon_bedrock` | Bedrock Converse Stream | 云凭据链（`AWS_PROFILE` / access key / bearer token 等，运行时自动识别） | 显式 prompt caching（`cachePoint`） |
| `github_copilot` | OpenAI Responses 等 | GitHub 浏览器设备码登录，或导入 `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN` 环境变量；按需换取 Copilot 短期 token，不落盘 | 不支持 thinking 档位 |
| `deepseek` | OpenAI Completions | API key（`DEEPSEEK_API_KEY`） | reasoner 型号推理不可调档 |
| `openrouter` | OpenAI Completions | API key（`OPENROUTER_API_KEY`) | 聚合网关 |
| `vercel_ai_gateway` | Anthropic Messages | API key（`AI_GATEWAY_API_KEY`） | 聚合网关 |
| `groq` | OpenAI Completions | API key（`GROQ_API_KEY`） | |
| `cerebras` | OpenAI Completions | API key（`CEREBRAS_API_KEY`） | |
| `mistral` | OpenAI Completions | API key（`MISTRAL_API_KEY`） | |
| `xai` | OpenAI Completions | API key（`XAI_API_KEY`） | |
| `zai` | OpenAI Completions | API key（`ZAI_API_KEY`） | |
| `huggingface` | OpenAI Completions | API key（`HF_TOKEN`） | |
| `minimax` / `minimax_cn` | Anthropic Messages | API key（`MINIMAX_API_KEY` / `MINIMAX_CN_API_KEY`） | 国际 / 国内两个端点 |
| `minimax_cn_coding_plan` | Anthropic Messages | API key（`MINIMAX_CN_API_KEY` / `MINIMAX_API_KEY`，与 `minimax_cn` 同账号同密钥） | "MiniMax Token Plan (CN)" coding 套餐 |
| `kimi_coding` | Anthropic Messages | API key（`KIMI_API_KEY` / `MOONSHOT_API_KEY`） | |
| `alibaba_token_plan_cn` | OpenAI Completions | 套餐 API key | 别名 `bailian` |
| `opencode` | OpenAI Completions 等 | API key（`OPENCODE_API_KEY`） | |

流式输出所有 provider 都支持（整个层建立在流式接口上）。多模态输入按模型而非按 provider 决定，来自各 provider 的模型目录数据，界面上以模型实际标注为准。prompt caching 只在上表标注处经代码核实。

`claude_code`、`chatgpt_subscription`、`claude_max_proxy` 等空目录是别名占位（`claude-max` → `claude-code`、`chatgpt-subscription` → `openai-codex`），行为由别名表决定，不是独立实现。`minimax_cn_coding_plan` 同样纯配置驱动，没有自己的代码。

## 自定义 provider

上表没覆盖的 OpenAI 兼容端点可以在 Web UI 的 Settings → Providers 里添加：必填项只有显示名和 base URL（不填 id 时从名字自动派生）。之后同一个 Fetch 按钮就能浏览该端点 `/models` 返回的模型列表，启用后无需改代码即可使用。自定义 provider 记在配置的 `providers.<id>` 下，标记 `source: "custom"`。

## 库方式使用

在自己的 Python 代码里创建 runtime，首选自动检测：

```python
from openprogram.providers.registry import create_runtime

runtime = create_runtime()                                        # 自动选第一个可用 provider
runtime = create_runtime(provider="anthropic", model="claude-sonnet-4-6")
```

六个内置 runtime 类可直接导入：

```python
from openprogram.providers import (
    AnthropicRuntime,     # Anthropic API
    OpenAIRuntime,        # OpenAI Responses API
    GeminiRuntime,        # Google Generative AI
    ClaudeCodeRuntime,    # Claude 订阅直连，无需 API key
    OpenAICodexRuntime,   # ChatGPT 订阅（Codex OAuth）
    GeminiCLIRuntime,     # 复用 Gemini CLI 登录态
)

runtime = AnthropicRuntime(api_key="sk-...", model="claude-sonnet-4-6")
runtime = OpenAICodexRuntime(model="gpt-5.5")
```

上表其余 provider 没有专属 runtime 类：`create_runtime(provider=..., model=...)` 会按该模型的协议自动路由，与聊天界面走同一条路径。
