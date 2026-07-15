# OpenClaw 集成指南

## 这是什么？

本指南介绍如何在 [OpenClaw](https://github.com/nicepkg/openclaw) 中使用 **Agentic Programming** — 作为 skill、工具库或 MCP tool provider。

Agentic Programming 和 OpenClaw 解决不同的问题：
- **OpenClaw** 编排 agent、管理会话、路由消息
- **Agentic Programming** 让单个函数具备思考能力（LLM-in-the-loop）

它们天然可组合：OpenClaw 的 skill 内部可以使用 agentic function。

## 配置

```bash
# 在 OpenClaw 工作区
cd ~/.openclaw/workspace

# 克隆 OpenProgram
git clone https://github.com/Fzkuji/OpenProgram.git

# 安装
cd OpenProgram
pip install -e .
```

## 用法 1：在 Skill 中使用 Agentic Function

最简单的集成方式 — 把 agentic function 作为 OpenClaw skill 的内部构建块。

**Skill 结构：**
```
~/.openclaw/workspace/skills/my-agentic-skill/
├── SKILL.md
└── scripts/
    └── analyze.py
```

**`scripts/analyze.py`：**
```python
#!/usr/bin/env python3
"""
使用 Agentic Programming 的 OpenClaw skill 脚本。
Agent 通过 exec 工具调用。
"""
import sys
import os

# 把 OpenProgram 加到 path（已 pip install -e 时不需要这行）
sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/OpenProgram"))

from openprogram import agentic_function
from openprogram.providers import ClaudeCodeRuntime

runtime = ClaudeCodeRuntime(model="haiku")


@agentic_function
def decompose(task):
    """把复杂任务拆解成可执行的步骤。"""
    return runtime.exec(content=[
        {"type": "text", "text": f"把这个任务拆解成 3-5 个具体、可执行的步骤：\n{task}\n\n编号，要具体。"},
    ])


@agentic_function
def assess(step):
    """评估一个步骤的难度和时间。"""
    return runtime.exec(content=[
        {"type": "text", "text": f"对这个步骤给出：难度（简单/中等/困难）和时间估计。\n格式：[难度] ~X小时\n\n步骤：{step}"},
    ])


@agentic_function
def plan(task):
    """为任务创建详细计划。"""
    steps_text = decompose(task=task)

    lines = [l.strip() for l in steps_text.split("\n") if l.strip() and l.strip()[0].isdigit()]
    assessments = []
    for line in lines[:5]:
        a = assess(step=line)
        assessments.append(f"{line}\n   → {a}")

    return "\n\n".join(assessments)


if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "构建一个带认证的 REST API"
    result = plan(task=task)
    print(result)
```

**`SKILL.md`：**
```markdown
# my-agentic-skill

使用 Agentic Programming 进行任务规划和分解，自动追踪上下文。

## 用法

当用户要求规划、分解或拆解任务时，运行：

\`\`\`bash
python3 ~/.openclaw/workspace/skills/my-agentic-skill/scripts/analyze.py "任务描述"
\`\`\`
```

## 用法 2：在 Agent 脚本中作为 Python 库

如果你的 OpenClaw agent 运行 Python 脚本，可以直接导入 agentic function：

```python
"""
OpenClaw agent 调用的代码审查脚本。
"""
from openprogram import agentic_function
from openprogram.providers import ClaudeCodeRuntime

runtime = ClaudeCodeRuntime(model="haiku")


@agentic_function
def review_code(code, language="python"):
    """审查代码的 bug、风格问题和改进建议。"""
    return runtime.exec(content=[
        {"type": "text", "text": f"审查这段 {language} 代码。列出：\n1. Bug（如果有）\n2. 风格问题\n3. 改进建议\n\n```{language}\n{code}\n```"},
    ])


@agentic_function
def suggest_tests(code):
    """为代码建议测试用例。"""
    return runtime.exec(content=[
        {"type": "text", "text": f"为这段代码建议 3 个测试用例。每个给出：测试名称、输入、期望输出。\n\n```python\n{code}\n```"},
    ])


@agentic_function
def code_analysis(code):
    """完整代码分析：审查 + 测试建议。"""
    review = review_code(code=code)
    tests = suggest_tests(code=code)
    return f"## 代码审查\n{review}\n\n## 建议测试\n{tests}"
```

## 用法 3：MCP Tool 封装

把 agentic function 封装为 OpenClaw 可以调用的 MCP tool：

```python
#!/usr/bin/env python3
"""
MCP 兼容的 tool server，暴露 agentic function。
"""
import json
import sys

from openprogram import agentic_function
from openprogram.providers import ClaudeCodeRuntime

runtime = ClaudeCodeRuntime(model="haiku")


@agentic_function
def summarize_text(text, style="bullet_points"):
    """按指定风格总结文本。"""
    style_instructions = {
        "bullet_points": "用 3-5 个要点总结。",
        "one_paragraph": "用一段话总结。",
        "eli5": "用 5 岁小孩能听懂的话解释。",
    }
    instruction = style_instructions.get(style, style_instructions["bullet_points"])

    return runtime.exec(content=[
        {"type": "text", "text": f"{instruction}\n\n文本：\n{text}"},
    ])


if __name__ == "__main__":
    request = json.loads(sys.stdin.read())
    tool = request.get("tool")
    args = request.get("args", {})

    if tool == "summarize":
        result = summarize_text(**args)
        print(json.dumps({"result": result}))
    else:
        print(json.dumps({"error": f"未知工具: {tool}"}))
```

## 为什么在 OpenClaw 中用 Agentic Programming？

| 不用 Agentic Programming | 用 Agentic Programming |
|---|---|
| Agent 在一次 LLM 调用中完成所有推理 | 推理拆分为聚焦的函数调用 |
| 上下文无限增长 | 上下文是结构化的 DAG，按函数作用域裁剪 |
| 难以调试 agent "想了什么" | 每次调用都记录为 session DAG 的节点，可在 Web UI 或 session 文件里回看 |
| 重试 = 重试整个 agent 回合 | 重试 = 只重试失败的函数 |

## 建议

1. **用 `ClaudeCodeRuntime` 快速上手** — 不需要额外 API key，登录过 Claude Code 就行，直接用订阅额度。详见 [Claude Code 集成](claude-code.md)。
2. **按计费方式选 runtime** — `ClaudeCodeRuntime` 走 Claude 订阅，`AnthropicRuntime` 走 Anthropic API key 计费。
3. **回看执行 trace** — 每次函数调用都记录在 session DAG 里，用 Web UI 或 `openprogram sessions list` 找到会话后回看。
4. **保持函数小而精** — 每个 `@agentic_function` 只做一件事，用 Python 组合它们。
