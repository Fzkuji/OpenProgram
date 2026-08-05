# Providers

> Source: [`openprogram/providers/`](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/providers/)

`create_runtime` 是所有 provider 的唯一构建入口。所有 provider 都经 OpenProgram 的 provider 层直接说原生 HTTP API——**不需要安装任何厂商 SDK**。CLI / 订阅类 provider 复用对应 CLI 工具的 OAuth 凭据,所以那些 CLI 需要装好并登录一次:

```bash
# Codex CLI(对应 openai-codex provider)
npm install -g @openai/codex && codex login

# Gemini CLI(对应 gemini-cli provider)
npm install -g @google/gemini-cli && gemini

# Claude Code CLI(对应 claude-code provider——收编其 OAuth token)
npm install -g @anthropic-ai/claude-code && claude login
```

API 型 provider 的 key 存在凭据库里:Web UI 的 **Settings → Providers**,或 `openprogram providers login <provider> --api-key`。

---

## create_runtime / detect_provider

```python
from openprogram.providers.registry import create_runtime, detect_provider, check_providers

rt = create_runtime()                                   # 自动检测最优可用 provider
rt = create_runtime(provider="anthropic")               # 显式 provider,用它的默认模型
rt = create_runtime(provider="openai-codex", model="gpt-5.5")
```

### `create_runtime(provider=None, model=None, **kwargs)`

返回可直接使用的 `Runtime`。`provider=None`(或 `"auto"`)会跑 `detect_provider()`。下面六个 provider 里,三个订阅/CLI 凭据型由专属 `Runtime` 子类支撑(`create_runtime` 的实现细节);三个 API-key 型就是基类 `Runtime` 本身——`create_runtime` 从凭据库解析 key 后构建 `Runtime("<命名空间>:<model>", api_key=...)`。**其它任何 provider 名**(deepseek、groq、openrouter、minimax、kimi 以及整个社区列表)经模型注册表走基类 `Runtime("provider:model", ...)`——与 chat dispatcher 同一条路。每个 runtime 都带权威的 `provider_id` 属性(由模型命名空间推导,订阅类自行设置)。`**kwargs` 转发给 runtime 构造函数。

### `detect_provider() -> (provider_name, default_model)`

检测优先级:

1. 环境变量 `AGENTIC_PROVIDER` / `AGENTIC_MODEL`
2. 配置文件(`~/.openprogram/config.json` → `default_provider` / `default_model`)
3. 调用方环境(正在 Codex CLI 里跑 → 就用它)
4. 可用 CLI 二进制(`codex` → `openai-codex`,`gemini` → `gemini-cli`)
5. 已存的 API key(anthropic → openai → google)

什么都没找到时抛带配置指引的 `RuntimeError`。

### `check_providers() -> dict`

六个专属 provider 的可用性报告:`{name: {"available": bool, "method": "CLI"|"API", "model": default}}`,`detect_provider()` 会选中的那个带 `"default": True`。

### `PROVIDERS` 表

| Provider 名 | 构建方式 | 默认模型 | 凭据 |
|------|------|------|------|
| `claude-code` | `ClaudeCodeRuntime` | `claude-sonnet-4`(别名,展开为当前 Sonnet) | Claude 订阅 OAuth(从 Claude Code CLI 收编) |
| `openai-codex` | `OpenAICodexRuntime` | `gpt-5.5` | ChatGPT 订阅 OAuth(`~/.codex/auth.json`) |
| `gemini-cli` | `GeminiCLIRuntime` | `gemini-2.5-flash` | Google 账号 OAuth(`~/.gemini/oauth_creds.json`) |
| `anthropic` | 基类 `Runtime("anthropic:<id>")` | `claude-sonnet-4-6` | Anthropic API key 或收编的订阅 OAuth token |
| `openai` | 基类 `Runtime("openai:<id>")` | `gpt-4.1` | OpenAI API key |
| `gemini` | 基类 `Runtime("google:<id>")` | `gemini-2.5-flash` | Google API key |

三个订阅类住在各自的 provider 包里;API-key 型 provider 没有类。构建一律走 `create_runtime(provider=...)`。

---

## anthropic

Anthropic Messages API,经 provider 层(流式、工具循环、DAG 记录全包)。`create_runtime` 解析凭据后直接返回基类 `Runtime("anthropic:<model>")`,`provider_id="anthropic"`——没有专属类。

```python
from openprogram.providers.registry import create_runtime

rt = create_runtime(provider="anthropic", api_key="sk-ant-...", model="claude-sonnet-4-6")
```

### 选项

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API key。`None` = 从凭据库解析——存好的 API key 或收编的 Claude 订阅 OAuth token(`sk-ant-oat...`,线路自动切换到 Bearer 认证) |
| `model` | `str` | `"claude-sonnet-4-6"` | `anthropic` provider 命名空间下的模型 id |
| `max_retries` | `int` | 环境变量 `OPENPROGRAM_MAX_RETRIES`,否则 6 | 转发给基类 `Runtime` 的重试预算 |

解析不到凭据时抛 `ValueError`。`list_models()` 返回已启用的 Anthropic 模型 id。

---

## openai

OpenAI Responses API,经 provider 层。`create_runtime` 解析 API key 后直接返回基类 `Runtime("openai:<model>")`,`provider_id="openai"`——没有专属类。

```python
rt = create_runtime(provider="openai", api_key="sk-...", model="gpt-4.1")
```

### 选项

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API key。`None` = 从凭据库解析(`openprogram providers login openai --api-key`) |
| `model` | `str` | `"gpt-4.1"` | `openai` provider 命名空间下的模型 id |
| `max_retries` | `int` | 环境变量 `OPENPROGRAM_MAX_RETRIES`,否则 6 | 转发给基类 `Runtime` 的重试预算 |

Azure 或本地 OpenAI 兼容服务:在设置页添加自定义 provider(Settings → Providers → Add custom provider,名称 + Base URL),再用 `Runtime(model="<provider>:<model>")` 或 `create_runtime(provider="<provider>")`。

---

## gemini

Google Gemini Generative Language API,经 provider 层。`create_runtime` 解析 API key 后直接返回基类 `Runtime("google:<model>")`,`provider_id="google"`——没有专属类;`gemini` provider 的模型走 `google` 注册表命名空间。

```python
rt = create_runtime(provider="gemini", api_key="...", model="gemini-2.5-flash")
```

### 选项

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | API key。`None` = 从凭据库解析(添加时接受的环境变量名:`GEMINI_API_KEY` / `GOOGLE_API_KEY` / `GOOGLE_GENERATIVE_AI_API_KEY`) |
| `model` | `str` | `"gemini-2.5-flash"` | `google` provider 命名空间下的模型 id |
| `max_retries` | `int` | 环境变量 `OPENPROGRAM_MAX_RETRIES`,否则 6 | 转发给基类 `Runtime` 的重试预算 |

---

## claude-code

用 **Claude 订阅** 跑 Claude——带订阅的 OAuth token 直连 `api.anthropic.com`(Bearer 认证 + Claude Code 身份 headers)。不走 API key 计费;token 每次调用现取,CLI 侧轮换自动跟上。由 `ClaudeCodeRuntime` 支撑。

```python
rt = create_runtime(provider="claude-code", model="claude-sonnet-4")
```

准备:用 Claude Code CLI 登录一次(`claude login`)让 OAuth token 可被收编,或用 `openprogram providers claude-code accounts add` 添加 Claude 账号。

### 选项(转发给 runtime)

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `api_key` | `str \| None` | `None` | 一般不填——token 每次调用从凭据库解析。传值会把它钉死(不推荐:订阅 token 会过期) |
| `model` | `str` | `"claude-sonnet-4"` | 裸的家族别名(`claude-opus-4` / `claude-sonnet-4` / `claude-haiku-4`)展开为该家族当前默认;更具体的 id(`claude-opus-4-8`、带日期的 id)原样透传 |
| `max_retries` | `int` | `2` | 转发给基类 `Runtime` 的重试预算 |

多余的关键字参数接受并忽略(向后兼容)。没有 Claude 凭据时抛 `ValueError`。

---

## openai-codex

ChatGPT / Codex **订阅** runtime。读取从 Codex CLI 的 `~/.codex/auth.json` 收编的 OAuth 凭据,访问 ChatGPT Responses 后端。刷新后的 token 会镜像回去,让 Codex CLI 保持同步。由 `OpenAICodexRuntime` 支撑。

```python
rt = create_runtime(provider="openai-codex", model="gpt-5.5")
```

准备:

```bash
npm install -g @openai/codex
codex login          # OAuth 登录——不要选 API-key 选项
```

### 选项(转发给 runtime)

| 参数 | 类型 | 默认值 | 说明 |
|------|------|------|------|
| `model` | `str` | `"gpt-5.5"` | Codex 模型 id(若带 `openai-codex:` 前缀会被剥掉) |
| `system` | `str \| None` | `None` | 可选 system prompt |
| `profile` | `str \| None` | 当前 profile | 使用哪个 OpenProgram auth profile(仅关键字) |

多余的关键字参数接受并忽略。必须是 OAuth 凭据——裸 OpenAI API key 抛 `AuthConfigError`(改用 `openai` provider)。

---

## gemini-cli

用 **Google 账号**(Gemini CLI OAuth)跑 Gemini。复用 `~/.gemini/oauth_creds.json`,以 HTTP 直连 Cloud Code Assist 后端——不起子进程。由 `GeminiCLIRuntime` 支撑。

```python
rt = create_runtime(provider="gemini-cli", model="gemini-2.5-flash")
```

准备:

```bash
npm install -g @google/gemini-cli
gemini               # 首次运行完成 OAuth 登录
```

### 选项(转发给 runtime)

| 参数 | 类型 | 默认值 | 说明 |
|------|------|------|------|
| `model` | `str` | `"gemini-2.5-flash"` | 模型 id;必须匹配注册表里的 `gemini-subscription/<id>` 条目 |
| `system` | `str \| None` | `None` | 可选 system prompt |
| `profile` | `str \| None` | 当前 profile | 使用哪个 OpenProgram auth profile(仅关键字) |

多余的关键字参数接受并忽略。只有 Google API key 的话,用 `gemini` provider。

---

## 其它所有 provider

其余 provider——deepseek、groq、openrouter、minimax、kimi 以及全目录——经模型注册表使用:

```python
from openprogram.agentic_programming.runtime import Runtime
rt = Runtime(model="deepseek:deepseek-chat")

# 或者等价地:
from openprogram.providers.registry import create_runtime
rt = create_runtime(provider="deepseek", model="deepseek-chat")
```

`create_runtime(provider=...)` 不给 model 时取该 provider 第一个已启用的模型;该 provider 还没有已注册模型时抛 `ValueError`(先在 Settings → Providers 或 `openprogram providers available <provider>` 启用一些)。

---

## 自定义 Provider

所有内置 provider 都是 `Runtime` 的子类。你可以用同样方式创建自己的:

```python
from openprogram.agentic_programming.runtime import Runtime

class MyRuntime(Runtime):
    def __init__(self, api_key, model="my-model"):
        super().__init__(model=model)
        self.api_key = api_key

    def _call(self, content, model="default", response_format=None):
        # 1. 把 content 块转成你的 API 格式
        # 2. 调用 API
        # 3. 返回 str
        texts = [b["text"] for b in content if b["type"] == "text"]
        return my_api_call("\n".join(texts), model=model)
```

关键点:`_call()` 接收 `content: list[dict]`,返回 `str`。就这么简单。(给基类 `Runtime` 传 `call=fn` 不用子类也能达到同样效果。)
