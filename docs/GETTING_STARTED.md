# 快速上手

## 🚀 3 分钟快速上手

### 第 1 步：安装

一条命令的安装脚本会把一切都装好——Python 包 + 网页 UI + 终端 UI + GUI agent（含模型权重和 OCR）：

```bash
git clone https://github.com/Fzkuji/OpenProgram.git && cd OpenProgram
./scripts/install.sh              # macOS/Linux   ·   Windows:  .\scripts\install.ps1
```

需要 Python ≥ 3.11、Node ≥ 20、git。默认安装会装齐所有轻量内容——网页 UI、TUI、Research / Wiki agent 程序、浏览器工具和 channels；GUI agent 按需安装（`openprogram programs install gui`，会下载 PyTorch）；`--minimal` 只装一个精简 host。完整依赖矩阵与参数见 [docs/install.md](install.md)。

### 第 2 步：接入一个 provider

无需单独执行命令——**第一次运行 `openprogram` 时，它会引导你完成 provider 配置**（从已登录的 Claude Code / Codex / Gemini CLI 导入凭据，或让你输入一个 API key），随后打开对话界面。随时可用 `openprogram setup` 重新运行。

也可以手动设置 key 跳过向导：

```bash
export ANTHROPIC_API_KEY=sk-ant-...                 # Claude
export OPENAI_API_KEY=sk-...                        # GPT
export GOOGLE_API_KEY=...                           # Gemini
# 或基于 CLI（无需 API key，使用你已有的订阅）：
#   npm i -g @anthropic-ai/claude-code && claude login
#   npm i -g @openai/codex && codex auth
#   npm i -g @google/gemini-cli && gemini auth login
```

确认检查：`openprogram providers` 会列出检测到的内容。

### 第 3 步：写你的第一个 agentic function

```python
from openprogram import agentic_function
from openprogram.providers.registry import create_runtime

runtime = create_runtime()                          # 自动选用第一个可用的 provider

@agentic_function
def greet(name):
    """用有创意、有趣的方式跟人打招呼。"""
    return runtime.exec(content=[
        {"type": "text", "text": f"Say hello to {name} in a creative way. Keep it short (1-2 sentences)."},
    ])

print(greet(name="World"))
```

```bash
python your_script.py
```

就这样。你的函数现在**会思考**了。

---

## 选择你的 Provider

Agentic Programming 开箱内置 6 个 runtime。选一个：

### 方案 A：通过 Meridian 代理使用 Claude 订阅（推荐新手上手使用）

**不需要 API key。** 通过本地 HTTP 桥接使用你的 Claude Code 订阅——`claude-code`
provider 与一个 Meridian daemon 通信（它在底层经由官方 Claude Code SDK 转发），
而不是去启动一个 CLI 进程。

**前置条件：**
```bash
# 1. Claude Code SDK + 登录（Meridian 经由它转发）
npm install -g @anthropic-ai/claude-code && claude login
# 2. Meridian 代理 daemon——暴露一个本地的 OpenAI 兼容端点
npm install -g @rynfar/meridian && meridian        # 监听 :3456
```

（如果你把 Meridian 跑在别处，用 `CLAUDE_MAX_PROXY_URL` 覆盖端口。）

**用法：**
```python
from openprogram.providers import ClaudeCodeRuntime

runtime = ClaudeCodeRuntime(model="haiku")
```

**优点：** 零 API-key 配置，使用你已有的订阅，完整的多模态内容
（不像旧的 `claude-max-api-proxy`）。
**缺点：** 多了一个本地 daemon；比直接用 API key 略慢。

---

### 方案 B：Anthropic API（Claude）

**最适合生产环境。** 直接 API 访问，带 prompt caching。

**配置：**
```bash
pip install -e .          # anthropic SDK is included by default
export ANTHROPIC_API_KEY="sk-ant-..."
```

**用法：**
```python
from openprogram.providers import AnthropicRuntime

runtime = AnthropicRuntime(
    model="claude-sonnet-4-6",
    # api_key="sk-ant-..."  # 或使用 ANTHROPIC_API_KEY 环境变量
)
```

**支持：** 文本、图片（base64/URL/文件）、prompt caching、系统提示词。

---

### 方案 C：OpenAI API（GPT）

**配置：**
```bash
pip install -e .          # openai SDK is included by default
export OPENAI_API_KEY="sk-..."
```

**用法：**
```python
from openprogram.providers import OpenAIRuntime

runtime = OpenAIRuntime(
    model="gpt-4o",
    # api_key="sk-..."  # 或使用 OPENAI_API_KEY 环境变量
)
```

**支持：** 文本、图片（base64/URL/文件）、response_format（JSON 模式）、系统提示词。

---

### 方案 D：Google Gemini API

**配置：**
```bash
pip install -e .          # google-genai SDK is included by default
export GOOGLE_API_KEY="..."
```

**用法：**
```python
from openprogram.providers import GeminiRuntime

runtime = GeminiRuntime(
    model="gemini-2.5-flash",
    # api_key="..."  # 或使用 GOOGLE_API_KEY 环境变量
)
```

**支持：** 文本、图片（base64/URL/文件）、系统指令、JSON schema 输出。

---

### 方案 E：Codex CLI

**不需要 Python 端的 API key。** 使用你已经登录过的 Codex CLI。

**前置条件：**
```bash
# 先安装 Codex CLI，然后登录
codex login --device-auth
```

**用法：**
```python
from openprogram.providers import OpenAICodexRuntime

runtime = OpenAICodexRuntime(model="gpt-5.5")
```

**优点：** 本地 CLI 工作流，便于复用已有的 Codex 配置。
**缺点：** 有子进程开销，仅支持文本。

---

### 方案 F：Gemini CLI

**不需要 Python 端的 API key。** 使用你机器上的 Gemini CLI 会话。

**前置条件：**
```bash
# 先安装 Gemini CLI，然后登录
gemini
```

**用法：**
```python
from openprogram.providers import GeminiCLIRuntime

runtime = GeminiCLIRuntime()
```

**优点：** 本地 CLI 工作流，无需 Python 端的 SDK 配置。
**缺点：** 有子进程开销，仅支持文本。

---

## 完整可运行示例

下面是一个完整脚本，复制、粘贴即可运行：

```python
"""
Full working example: Task decomposition with Agentic Programming.
Uses ClaudeCodeRuntime (no API key needed, just `claude` CLI).
"""
from openprogram import agentic_function
from openprogram.providers import ClaudeCodeRuntime

# 初始化 runtime（不需要 API key）
runtime = ClaudeCodeRuntime(model="haiku")


@agentic_function
def analyze(topic):
    """分析一个话题并列出 3 个关键点。"""
    return runtime.exec(content=[
        {"type": "text", "text": f"List exactly 3 key points about: {topic}\nOne line per point, numbered 1-3."},
    ])


@agentic_function
def elaborate(point):
    """用一句有洞察力的话展开单个观点。"""
    return runtime.exec(content=[
        {"type": "text", "text": f"Elaborate on this point in exactly one insightful sentence:\n{point}"},
    ])


@agentic_function
def research(topic):
    """分析一个话题，然后展开每个要点。"""
    # 第 1 步：获取关键点（Python 控制流程）
    points_text = analyze(topic=topic)
    print(f"📋 Key points:\n{points_text}\n")

    # 第 2 步：展开每个要点（Python 控制循环）
    lines = [l.strip() for l in points_text.split("\n") if l.strip() and l.strip()[0].isdigit()]
    for line in lines[:3]:
        detail = elaborate(point=line)
        print(f"  💡 {detail}\n")

    # 第 3 步：返回总结（LLM 自动看到完整上下文）
    return runtime.exec(content=[
        {"type": "text", "text": "Based on the analysis above, write a one-paragraph summary."},
    ])


if __name__ == "__main__":
    result = research(topic="Why Rust is gaining popularity in systems programming")
    print(f"\n📝 Summary:\n{result}")
```

保存为 `demo.py`，用 `python demo.py` 运行。

---

## 核心概念

| 概念 | 它是什么 |
|---------|-----------|
| `@agentic_function` | 装饰器。将每次调用记录为 session DAG 中的一个节点 |
| `runtime.exec()` | 调用 LLM——上下文从 DAG 自动算出 |
| Session DAG | 每条用户消息 / LLM 调用 / 函数调用都是一个节点——见 `openprogram/context/` |
| Docstring | 描述函数本身；本次调用的 prompt 写在 `runtime.exec(content=...)` 里 |

### 核心模式

```python
@agentic_function
def my_function(param):
    """这个 docstring 就是 prompt。LLM 会读到它。"""

    data = do_something_deterministic(param)   # Python：确定性执行
    result = runtime.exec(content=[...])       # LLM：推理步骤
    return result                              # Python：确定性返回
```

**Python 控制流程。LLM 做推理。这就是全部思想。**

---

## 下一步

- 📖 [API 参考](API.md)
- 🔗 [Claude Code 集成指南](INTEGRATION_CLAUDE_CODE.md) — 无需任何 API key 即可使用
- 🔗 [OpenClaw 集成指南](INTEGRATION_OPENCLAW.md) — 作为 OpenClaw skill/tool 使用
- 📂 [示例](../examples/) — 更多可运行的 demo
