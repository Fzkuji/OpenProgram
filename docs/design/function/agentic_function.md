# @agentic_function

## 概述

`@agentic_function` 是需要 LLM 参与的函数。装饰器自动将函数执行记录到 Context Tree 中。

核心规则：**一个 @agentic_function 可以调用多次 `runtime.exec()`（每次创建一个 exec 子节点），也可以调用任意多个其他 @agentic_function。**

## 三种使用模式

### 1. 叶子函数

单一任务，调一次 `exec()`，返回结果。不调其他子函数。

```python
@agentic_function
def translate_to_chinese(text: str, runtime: Runtime) -> str:
    """将英文文本翻译为中文。

    Args:
        text: 需要翻译的英文文本。

    Returns:
        翻译后的中文文本。
    """
    return runtime.exec(content=[
        {"type": "text", "text": f"Translate to Chinese:\n\n{text}"},
    ])
```

Context tree:
```
translate_to_chinese  ✓ success
└── _exec → "翻译后的中文文本"
```

### 2. 编排函数

按固定顺序调用多个子函数，Python 代码决定顺序。`exec()` 可选。

```python
@agentic_function
def research_pipeline(task: str, runtime: Runtime) -> dict:
    """执行完整研究流程：调研 → 找 gap → 生成想法。

    Args:
        task: 研究主题。
        runtime: LLM 运行时实例。

    Returns:
        包含 survey、gaps、ideas 的结果字典。
    """
    survey = survey_topic(topic=task, runtime=runtime)

    # 步骤之间可以插入普通 Python 处理
    key_points = extract_key_points(survey)

    gaps = identify_gaps(survey=key_points, runtime=runtime)
    ideas = generate_ideas(gaps=gaps, runtime=runtime)

    return {"survey": survey, "gaps": gaps, "ideas": ideas}
```

Context tree:
```
research_pipeline
├── survey_topic
├── identify_gaps
└── generate_ideas
```

### 3. 动态调用（LLM 选择函数，tool_use）

把子函数塞进 `runtime.exec(tools=[...])`，让 provider 原生 tool_use 协议处理
分发。`@agentic_function` 自带 `.spec`（JSON Schema，自动从签名 + docstring
生成）和 `.execute`，不需要写任何 build_options/render_options/parse_args 中间层。

```python
@agentic_function
def research_assistant(task: str, runtime: Runtime) -> str:
    """分析任务，让 LLM 选择合适的子函数。

    Args:
        task: 用户的任务描述。
        runtime: LLM 运行时实例。

    Returns:
        LLM 在调完所有工具后给出的最终回复。
    """
    return runtime.exec(
        content=[{"type": "text", "text": task}],
        tools=[summarize_text, polish_text, translate_to_chinese],
        tool_choice="auto",   # "required" 强制调一个；指定 name 强制选某个
    )
```

Context tree:
```
research_assistant
└── polish_text        ← LLM 选择的
```

工作原理：

1. Runtime 把 `[summarize_text, polish_text, translate_to_chinese]` 转成 JSON
   Schema 工具声明发给 LLM。
2. LLM 决定调某一个，吐一个 `function_call` 事件。
3. Runtime 本地调对应的 Python 函数，结果以 `function_call_output` 塞回。
4. 循环直到 LLM 吐纯文本作为最终回复。

## Tool spec 自动生成

`agentic_function.spec` 从 Python 函数签名 + docstring + `input=` 元数据自动生成：

- 函数名 → tool name
- docstring → tool description
- 参数类型注解 → JSON Schema type
- 有默认值 → optional，没有 → required
- `runtime: Runtime` 这类注入参数 → 自动从 schema 剔除
- `input={"x": {"hidden": True}}` → 也会剔除

不满意自动生成的 spec？直接覆盖 `.spec` 属性即可。

## tool_choice

| 值 | 效果 |
|---|---|
| `"auto"`（默认） | LLM 自己决定调不调 |
| `"required"` | 必须调至少一个 tool（从 tools 列表里选） |
| `"none"` | 禁止调 tool |
| `{"type":"function","name":"X"}` | 必须调指定的 X |

配合 `parallel_tool_calls=False` 可进一步强制"只调一次"。

## 容错机制

原生 tool_use 协议已经消化了旧 render_options 时代需要手动处理的大部分 case：

| 情况 | 处理 |
|------|------|
| 函数名写错 | 协议层限制，只能从 tools 列表里选 |
| 多余 / 缺失参数 | JSON Schema 校验失败，模型按描述重填 |
| JSON 解析失败 | 没有文本解析环节 |
| 工具执行异常 | runtime 把异常作为 function_call_output 喂回 |
| 循环不收敛 | runtime 到 `max_iterations` 抛 RuntimeError |

## Docstring 规范

Docstring 在每次 `runtime.exec()` 时会被当作 prompt 传给 LLM（对 session provider 是首次，对无 session provider 是每次）。因此它的写法直接决定 LLM 的行为。

### 必须
- 一行摘要（函数做什么）
- 具体指令（输出格式、约束、禁止项）
- Args + Returns

### 禁止：角色/Persona 框架

**不要写 "You are a senior ML researcher"、"You are a dispatcher"、"You are a creative brainstormer" 这种 persona 开头。**

原因：
1. **Client 本身已经有 system prompt。** Codex CLI、Claude Code、Gemini CLI 都带着自己的基础 system prompt（"You are Codex, an AI coding agent..."）。我们再加一层 persona 是叠床架屋，而且会和 client 自己的定位打架。
2. **每个函数不同 persona 对 session provider 是灾难。** Codex / Claude Code 这类 session-based CLI 在一次会话里持续累积 prompt。如果每个 `@agentic_function` 都在 docstring 里换一个角色（researcher → dispatcher → reviewer → …），LLM 的 context 里会堆满互相矛盾的 persona，行为混乱还浪费 token。
3. **Persona 会触发 agentic CLI 的工具调用本能。** 给 Codex 看到 "You are a senior ML researcher managing a research project" + 一个任务路径，它的内心戏就是"senior researcher 会先去看看 survey"→ 开始跑 `sed` / `cat` 读文件，而不是老实返回我们要的 JSON。给决策类函数加 persona，实际效果是把 planner 变成 executor。

### 正确写法

直接说**这个函数此刻要做什么、可选项是什么、怎么挑、返回什么格式**：

```python
# ❌ 错误：加 persona
"""You are a senior ML researcher managing a research project.
Based on the task and what has been done so far, pick the next stage.
Return JSON: {...}"""

# ✅ 正确：直接说任务
"""Pick the next research stage for this task.

Available stages:
{stages}

Return JSON:
{
  "stage": "stage_name",
  "sub_task": "specific goal",
  "done": false
}

Pick by:
- If no literature review has run yet → "literature"
- If literature is done but no ideas → "idea"
- ...
"""
```

### Planner / dispatcher / router 类函数

只需要返回 JSON 的决策函数，不要用 "Do NOT run commands / read files / use tools" 这类负向禁令。如果 agent CLI 跑去调工具而不是直接返回决策，说明 prompt 还不够明确 —— 修法是把 how-to-choose 的判断标准写得更具体（"Pick by: <criterion A>, <criterion B>, ..."），而不是在前面堆禁令。

### 其他禁止
- "You are a helpful assistant" / "Complete the task" —— 空话
- 重复 content 里已经给的数据
- "Please"、"I'd like you to" —— 礼貌语对 LLM 无意义，占 token

## Content 规范

`runtime.exec(content=[...])` 只放数据：

```python
# 正确
runtime.exec(content=[{"type": "text", "text": text}])

# 错误
runtime.exec(content=[{"type": "text", "text": f"Please analyze: {text}. Return one word."}])
```

## 内置原子工具

放在 `openprogram/tools/<name>/`，每个工具一个目录，对齐 Claude Code 风格：

| tool | 目录 | 作用 |
|------|------|------|
| `bash` | `tools/bash/` | 跑 shell 命令 |
| `read` | `tools/read/` | 按行读文件（支持 offset/limit） |
| `write` | `tools/write/` | 创建或覆盖文件 |
| `edit` | `tools/edit/` | 按字符串替换编辑文件 |
| `glob` | `tools/glob/` | 文件名模式匹配 |
| `grep` | `tools/grep/` | 内容正则搜索（优先调 ripgrep） |

用法：

```python
from openprogram.tools import get_many

reply = runtime.exec(
    content=[{"type": "text", "text": "列出 cwd 下所有 Python 文件"}],
    tools=get_many(["bash", "glob"]),
)
```

## 完整样例

见 `openprogram/programs/functions/third_party/llm_call_example.py`。
