<p align="center">
  <img src="../docs/images/banner.png" alt="Agentic Programming: 重新定义 Agent 流程控制" width="900">
</p>

<p align="center">
  <h1 align="center">🧬 Agentic Programming</h1>
  <p align="center">
    <strong>会思考的 Python 函数。</strong><br>
    一种 Python 与大模型协同执行函数的编程范式。
  </p>
  <p align="center">
    <a href="../README.md">🇺🇸 English</a>
  </p>
</p>

## 目录

- [动机](#动机)
- [核心思想](#核心思想)
- [快速开始](#快速开始)
- [用法](#用法)
  - [Python](#1-python--编写-agentic-代码)
  - [Skills](#2-skills--agent-集成)
- [核心概念](#核心概念)
  - [Agentic 函数](#agentic-函数)
  - [自动上下文](#自动上下文)
  - [自我演化代码](#自我演化代码)
  - [错误恢复](#错误恢复)
- [API 参考](#api-参考)
- [对比](#对比)
- [项目结构](#项目结构)
- [贡献](#贡献)

---

> 🚀 **这是一个范式提案。** 我们提出了一种全新的 LLM 编程思路。这里的代码是参考实现——欢迎你基于这些想法，用任何语言、任何场景，构建自己的版本。

**基于 Agentic Programming 构建的项目：**

| 项目 | 描述 |
|------|------|
| [🖥️&nbsp;GUI&nbsp;Agent&nbsp;Harness](https://github.com/Fzkuji/GUI-Agent-Harness) | 通过视觉 + agentic 函数操控桌面应用的自主 GUI agent。Python 控制 observe→plan→act→verify 循环；LLM 仅在被要求时进行推理。 |

---

## 动机

当前的 LLM agent 框架将 LLM 置于中央调度器的位置——由它决定做什么、何时做、怎么做。这带来了三个根本问题：

- **不可预测的执行** — LLM 可能跳过、重复或自行发明步骤，无视预设的工作流
- **上下文爆炸** — 每次工具调用往返都会累积历史记录
- **没有输出保证** — LLM 是在"理解"指令，而非"执行"指令

<p align="center">
  <img src="../docs/images/the_problem.png" alt="问题：LLM 作为调度器" width="800">
</p>

核心问题：**LLM 控制了流程，但没有任何东西能强制执行它。** Skills、prompts 和系统消息只是建议，不是保证。

---

## 核心思想

<p align="center">
  <img src="../docs/images/the_idea.png" alt="范式：Python 控制流程，LLM 负责推理" width="800">
</p>

**把流程控制权还给 Python。让 LLM 专注推理。**

| 原则 | 方式 |
|------|------|
| **确定性流程** | Python 控制 `if/else/for/while`。执行路径是保证的，不是建议的。 |
| **最少 LLM 调用** | 只在需要推理时调用 LLM。2 次调用，不是 10 次。 |
| **Docstring = Prompt** | 改函数的 docstring，就改了 LLM 的行为。不需要单独的 prompt 文件。 |
| **自我演化** | 函数在运行时通过元函数生成、修复和改进自身。 |

```python
@agentic_function
def observe(task):
    """Look at the screen and describe what you see."""
    
    img = take_screenshot()       # Python: deterministic
    ocr = run_ocr(img)            # Python: deterministic
    
    return runtime.exec(content=[ # LLM: reasoning
        {"type": "text", "text": f"Task: {task}\nOCR: {ocr}"},
        {"type": "image", "path": img},
    ])
```

---

## 快速开始

```bash
pip install agentic-programming
```

或从源码安装用于开发：

```bash
git clone https://github.com/Fzkuji/Agentic-Programming.git
cd Agentic-Programming
pip install -e .
```

至少设置一个 LLM 提供方：

| 提供方 | 设置 |
|--------|------|
| Claude Code CLI | `npm i -g @anthropic-ai/claude-code && claude login` |
| Codex CLI | `npm i -g @openai/codex && codex auth` |
| Gemini CLI | `npm i -g @anthropic-ai/gemini-cli` |
| Anthropic API | `pip install -e ".[anthropic]"` 然后 `export ANTHROPIC_API_KEY=...` |
| OpenAI API | `pip install -e ".[openai]"` 然后 `export OPENAI_API_KEY=...` |
| Gemini API | `pip install -e ".[gemini]"` 然后 `export GOOGLE_API_KEY=...` |

使用 `agentic providers` 验证配置。

---

## 用法

### 1. Python — 编写 agentic 代码

```python
from agentic import agentic_function, create_runtime

runtime = create_runtime()  # auto-detects best available provider

@agentic_function
def summarize(text: str) -> str:
    """Summarize the given text into 3 bullet points."""
    return runtime.exec(content=[
        {"type": "text", "text": text},
    ])

result = summarize(text="Your long article here...")
```

需要时可以指定提供方：

```python
runtime = create_runtime(provider="openai", model="gpt-4o")
```

### 2. Skills — agent 集成

安装 skills，让你的 LLM agent 能够通过自然语言使用 agentic 函数：

```bash
cp -r skills/* ~/.claude/skills/    # Claude Code
cp -r skills/* ~/.gemini/skills/    # Gemini CLI
```

然后与你的 agent 对话：

> "创建一个从文本中提取邮箱地址的函数"

Agent 会识别 skill，调用 `agentic create`，生成的函数会处理后续所有操作。创建完成后：

> "对 'This is amazing' 进行情感分析"

---

## 核心概念

### Agentic 函数

每个 `@agentic_function` 都可以调用 `runtime.exec()` 来请求 LLM。框架自动将执行上下文注入到 prompt 中。Python 控制流程——LLM 仅在被显式要求时进行推理。

```python
@agentic_function
def login_flow(username, password):
    """Complete login flow."""
    observe(task="find login form")       # Python decides what to do
    click(element="login button")         # Python decides the order
    return verify(expected="dashboard")   # Python decides when to stop
```

### 自动上下文

每次调用创建一个 **Context** 节点。节点组成树，自动注入到 LLM 调用中：

```
login_flow ✓ 8.8s
├── observe ✓ 3.1s → "found login form at (200, 300)"
├── click ✓ 2.5s → "clicked login button"
└── verify ✓ 3.2s → "dashboard confirmed"
```

当 `verify` 调用 LLM 时，它自动看到 `observe` 和 `click` 的返回结果。不需要手动管理上下文。

### 自我演化代码

函数可以生成新函数、修复损坏的函数、搭建完整的应用——全部在运行时完成：

```python
from agentic.meta_functions import create, create_app, fix

# Generate a function from description
sentiment = create("Analyze text sentiment", runtime=runtime, name="sentiment")
sentiment(text="I love this!")  # → "positive"

# Generate a complete app (runtime + argparse + main)
create_app("Summarize articles from URLs", runtime=runtime, name="summarizer")
# → agentic/apps/summarizer.py — runnable with: python agentic/apps/summarizer.py <url>

# Fix a broken function — auto-reads source & error history
fixed = fix(fn=broken_fn, runtime=runtime, instruction="return JSON, not plain text")
```

`create → run → fail → fix → run` 循环意味着程序在使用中自我改进。

### 错误恢复

`Runtime` 自动重试瞬时故障。对于更深层的问题，`fix()` 会重写函数：

```python
runtime = create_runtime(max_retries=3)

try:
    result = extract(text="Acme closed at $42.50")
except Exception:
    extract = fix(fn=extract, runtime=runtime)  # LLM analyzes errors and rewrites
    result = extract(text="Acme closed at $42.50")
```

每次尝试都记录在 Context 树中——`fix()` 读取完整的错误历史来诊断根本原因，而不仅仅是表面症状。

---

## API 参考

### 核心

| 导入 | 功能 |
|------|------|
| `from agentic import agentic_function` | 装饰器。将执行记录到 Context 树 |
| `from agentic import Runtime` | LLM 运行时。`exec()` 调用 LLM 并自动注入上下文 |
| `from agentic import Context` | 执行树。`tree()`、`save()`、`traceback()` |
| `from agentic import create_runtime` | 创建 Runtime，支持自动检测或指定提供方 |

### 元函数

| 导入 | 功能 |
|------|------|
| `from agentic.meta_functions import create` | 从描述生成新的 `@agentic_function` |
| `from agentic.meta_functions import create_app` | 生成包含 `main()` 的完整可运行应用 |
| `from agentic.meta_functions import fix` | 通过 LLM 分析修复损坏的函数 |
| `from agentic.meta_functions import create_skill` | 生成用于 agent 发现的 SKILL.md |

### 提供方

六个内置提供方：Anthropic、OpenAI、Gemini (API)、Claude Code、Codex、Gemini (CLI)。所有 CLI 提供方在调用之间维持**会话连续性**。详见 [Provider 文档](api/providers.md)。

---

## 对比

|  | Tool-Calling / MCP | Agentic Programming |
|--|---------------------|---------------------|
| **谁调度？** | LLM 决定 | Python 决定 |
| **函数包含** | 纯代码 | 代码 + LLM 推理 |
| **上下文** | 扁平的对话 | 结构化的树 |
| **Prompt** | 隐藏在 agent 配置中 | Docstring = prompt |
| **自我改进** | 未内置 | `create` → `fix` → 演化 |

MCP 是*传输层*。Agentic Programming 是*执行模型*。两者正交。

---

## 项目结构

```
agentic/
├── __init__.py              # agentic_function, Runtime, Context, create_runtime
├── function.py              # @agentic_function decorator
├── runtime.py               # Runtime (exec + retry + context injection)
├── context.py               # Context tree
├── meta_functions/          # Self-evolving code generation
│   ├── create.py            #   create() — generate a function
│   ├── create_app.py        #   create_app() — generate a complete app
│   ├── fix.py               #   fix() — rewrite broken functions
│   └── create_skill.py      #   create_skill() — generate SKILL.md
├── providers/               # Anthropic, OpenAI, Gemini, Claude Code, Codex, Gemini CLI
├── functions/               # saved generated functions
└── apps/                    # generated apps (from create_app)
skills/                      # SKILL.md files for agent integration
examples/                    # runnable demos
tests/                       # pytest suite
```

## 集成

| 指南 | 描述 |
|------|------|
| [Getting Started](GETTING_STARTED.md) | 3 分钟上手及可运行示例 |
| [Claude Code](INTEGRATION_CLAUDE_CODE.md) | 通过 Claude Code CLI 使用，无需 API key |
| [OpenClaw](INTEGRATION_OPENCLAW.md) | 作为 OpenClaw skill 使用 |
| [API Reference](API.md) | 完整 API 文档 |

---

## 贡献

这是一个**范式提案**及其参考实现。我们欢迎讨论、其他语言的替代实现、验证或挑战该方法的用例，以及 bug 报告。

详见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

## 许可证

MIT
