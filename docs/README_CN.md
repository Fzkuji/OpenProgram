<p align="center">
  <img src="../docs/images/banner.png" alt="Agentic Programming" width="800">
</p>

<h1 align="center">Agentic Programming</h1>

<p align="center">
  <strong>会思考的 Python 函数。</strong><br>
  一种 Python 控制流程、LLM 负责推理的编程范式。
</p>

<p align="center">
  <a href="https://pypi.org/project/agentic-programming/"><img src="https://img.shields.io/pypi/v/agentic-programming?color=blue" alt="PyPI"></a>
  <a href="https://pepy.tech/project/agentic-programming"><img src="https://static.pepy.tech/badge/agentic-programming" alt="Downloads"></a>
  <a href="https://github.com/Fzkuji/Agentic-Programming/actions/workflows/ci.yml"><img src="https://github.com/Fzkuji/Agentic-Programming/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/Fzkuji/Agentic-Programming/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Fzkuji/Agentic-Programming" alt="License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/pypi/pyversions/agentic-programming" alt="Python"></a>
</p>

<p align="center">
  <a href="GETTING_STARTED.md">快速上手</a> &middot;
  <a href="API.md">API 参考</a> &middot;
  <a href="../README.md">English</a>
</p>

---

> **这是一个范式提案。** 当前的 LLM agent 框架让 LLM 控制一切——做什么、何时做、怎么做。结果？不可预测的执行、上下文爆炸、没有输出保证。我们反转这一切：**Python 控制流程，LLM 只在被要求时推理。**

```python
from agentic import agentic_function, create_runtime

runtime = create_runtime()  # 自动检测最佳 provider

@agentic_function
def summarize(text: str) -> str:
    """将给定的文本总结为 3 个要点。"""
    return runtime.exec(content=[{"type": "text", "text": text}])

result = summarize(text="你的长文章...")
```

## 快速开始

```bash
pip install agentic-programming
```

至少设置一个 LLM 提供方：

| 提供方 | 设置 |
|--------|------|
| Claude Code CLI | `npm i -g @anthropic-ai/claude-code && claude login` |
| Codex CLI | `npm i -g @openai/codex && codex auth` |
| Gemini CLI | `npm i -g @google/gemini-cli` |
| Anthropic API | `pip install "agentic-programming[anthropic]"` 然后 `export ANTHROPIC_API_KEY=...` |
| OpenAI API | `pip install "agentic-programming[openai]"` 然后 `export OPENAI_API_KEY=...` |
| Gemini API | `pip install "agentic-programming[gemini]"` 然后 `export GOOGLE_API_KEY=...` |

使用 `agentic providers` 验证配置。

## 为什么选择 Agentic Programming?

<p align="center">
  <img src="../docs/images/the_idea.png" alt="Python 控制流程，LLM 负责推理" width="800">
</p>

| 原则 | 方式 |
|------|------|
| **确定性流程** | Python 控制 `if/else/for/while`。执行路径是保证的，不是建议的。 |
| **最少 LLM 调用** | 只在需要推理时调用 LLM。2 次调用，不是 10 次。 |
| **Docstring = Prompt** | 改函数的 docstring，就改了 LLM 的行为。不需要单独的 prompt 文件。 |
| **自我演化** | 函数在运行时生成、修复和改进自身。 |

<details>
<summary><strong>当前框架的问题</strong></summary>

<p align="center">
  <img src="../docs/images/the_problem.png" alt="LLM 作为调度器" width="800">
</p>

当前的 LLM agent 框架将 LLM 置于中央调度器的位置。这带来了三个根本问题：

- **不可预测的执行** — LLM 可能跳过、重复或自行发明步骤，无视预设的工作流
- **上下文爆炸** — 每次工具调用往返都会累积历史记录
- **没有输出保证** — LLM 是在"理解"指令，而非"执行"指令

核心问题：**LLM 控制了流程，但没有任何东西能强制执行它。** Skills、prompts 和系统消息只是建议，不是保证。

</details>

|  | Tool-Calling / MCP | Agentic Programming |
|--|---------------------|---------------------|
| **谁调度？** | LLM 决定 | Python 决定 |
| **函数包含** | 纯代码 | 代码 + LLM 推理 |
| **上下文** | 扁平的对话 | 结构化的树 |
| **Prompt** | 隐藏在 agent 配置中 | Docstring = prompt |
| **自我改进** | 未内置 | `create` → `fix` → 演化 |

MCP 是*传输层*。Agentic Programming 是*执行模型*。两者正交。

## 用法

### Python

```python
@agentic_function
def login_flow(username, password):
    """完成登录流程。"""
    observe(task="find login form")       # Python 决定做什么
    click(element="login button")         # Python 决定顺序
    return verify(expected="dashboard")   # Python 决定何时停止
```

每次调用创建一个 **Context** 节点。节点组成树，自动注入到 LLM 调用中：

```
login_flow ✓ 8.8s
├── observe ✓ 3.1s → "found login form at (200, 300)"
├── click ✓ 2.5s → "clicked login button"
└── verify ✓ 3.2s → "dashboard confirmed"
```

当 `verify` 调用 LLM 时，它自动看到 `observe` 和 `click` 的返回结果。不需要手动管理上下文。

### Skills — agent 集成

安装 skills，让你的 LLM agent 能够通过自然语言使用 agentic 函数：

```bash
cp -r skills/* ~/.claude/skills/    # Claude Code
cp -r skills/* ~/.gemini/skills/    # Gemini CLI
```

然后与你的 agent 对话：

> "创建一个从文本中提取邮箱地址的函数"

### MCP — 任意 MCP 客户端

```bash
pip install "agentic-programming[mcp]"
```

添加到你的 MCP 客户端配置：

```json
{
    "mcpServers": {
        "agentic": {
            "command": "python",
            "args": ["-m", "agentic.mcp"]
        }
    }
}
```

暴露五个工具：`list_functions`、`run_function`、`create_function`、`create_application`、`fix_function`。

### 自我演化代码

函数可以生成新函数、修复损坏的函数、搭建完整的应用——全部在运行时完成：

```python
from agentic.meta_functions import create, create_app, fix

# 从描述生成函数
sentiment = create("Analyze text sentiment", runtime=runtime, name="sentiment")
sentiment(text="I love this!")  # → "positive"

# 生成包含 main() 的完整可运行应用
create_app("Summarize articles from URLs", runtime=runtime, name="summarizer")
# → agentic/apps/summarizer.py

# 修复损坏的函数——自动读取源码和错误历史
fixed = fix(fn=broken_fn, runtime=runtime, instruction="return JSON, not plain text")
```

`create → run → fail → fix → run` 循环意味着程序在使用中自我改进。

## 生态系统

| 项目 | 描述 |
|------|------|
| [GUI&nbsp;Agent&nbsp;Harness](https://github.com/Fzkuji/GUI-Agent-Harness) | 通过视觉 + agentic 函数操控桌面应用的自主 GUI agent。Python 控制 observe→plan→act→verify 循环；LLM 仅在被要求时进行推理。 |
| [Research&nbsp;Agent&nbsp;Harness](https://github.com/Fzkuji/Research-Agent-Harness) | 自主研究 agent：文献调研 → idea 生成 → 实验 → 论文写作 → 跨模型审稿。从选题到投稿的全流程自动化。 |

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

## 集成

| 指南 | 描述 |
|------|------|
| [Getting Started](GETTING_STARTED.md) | 3 分钟上手及可运行示例 |
| [Claude Code](INTEGRATION_CLAUDE_CODE.md) | 通过 Claude Code CLI 使用，无需 API key |
| [OpenClaw](INTEGRATION_OPENCLAW.md) | 作为 OpenClaw skill 使用 |
| [API Reference](API.md) | 完整 API 文档 |

<details>
<summary><strong>项目结构</strong></summary>

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
├── mcp/                     # MCP server (python -m agentic.mcp)
├── functions/               # saved generated functions
└── apps/                    # generated apps (from create_app)
skills/                      # SKILL.md files for agent integration
examples/                    # runnable demos
tests/                       # pytest suite
```

</details>

## 贡献

这是一个**范式提案**及其参考实现。我们欢迎讨论、其他语言的替代实现、验证或挑战该方法的用例，以及 bug 报告。

详见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

## 许可证

MIT
