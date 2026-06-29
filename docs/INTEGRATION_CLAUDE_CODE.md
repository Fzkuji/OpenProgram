# Claude Code 集成指南

## 这是什么？

`ClaudeCodeRuntime` 让你**无需任何 API key** 即可使用 Agentic Programming。它通过 [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) 路由 LLM 调用，使用你的 Claude Code 订阅。

只要你已安装并登录 `claude`，就可以直接上手。

## 前置条件

1. **安装 Claude Code CLI：**
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```

2. **登录：**
   ```bash
   claude login
   ```

3. **验证可用：**
   ```bash
   claude -p "Hello, world!"
   ```

需要的配置仅此而已。不需要 API key，也不需要环境变量。

## 基本用法

```python
from openprogram import agentic_function
from openprogram.providers import ClaudeCodeRuntime

# 不需要 API key —— 使用 Claude Code 订阅
runtime = ClaudeCodeRuntime(model="haiku")

@agentic_function
def explain(concept):
    """清晰简洁地解释一个概念。"""
    return runtime.exec(content=[
        {"type": "text", "text": f"Explain '{concept}' in 2-3 sentences. Be clear and concise."},
    ])

result = explain(concept="gradient descent")
print(result)
```

## 配置选项

```python
runtime = ClaudeCodeRuntime(
    model="haiku",       # 模型名称（传给 --model 参数）
    timeout=120,          # 每次 CLI 调用的最长秒数（默认 120）
    cli_path=None,        # claude 二进制文件路径（自动检测）
)
```

### 模型名称

`model` 参数会直接传给 `claude -p --model <model>`。常用取值：

| 模型 | 说明 |
|-------|-------------|
| `"sonnet"` | Claude Sonnet（默认，快速且能力强） |
| `"opus"` | Claude Opus（能力最强） |
| `"haiku"` | Claude Haiku（最快、最便宜） |

## 工作原理

在底层，`ClaudeCodeRuntime` 的流程是：

1. 把所有 content block 合并为一段文本 prompt
2. 以子进程方式调用 `claude -p <prompt>`
3. 返回 CLI 的 stdout 作为结果

```
你的 Python 代码
    → @agentic_function 装饰器（记录一个 DAG 节点）
        → runtime.exec()（从 DAG 构建 prompt）
            → claude -p "..."（CLI 调用）
                → Claude API（经由订阅）
            ← 响应文本
        ← 回复作为 DAG 节点写回
    ← 返回值
```

## 限制

- **仅支持文本。** 图片、音频和文件 block 会被转换为文本占位符（`[Image: path]`）。如需多模态输入，请使用带 API key 的 `AnthropicRuntime`。
- **子进程开销。** 每次调用都会启动一个新进程（约 0.5-1 秒开销）。对延迟敏感的应用请使用直连 API 的 provider。
- **不支持流式输出。** 结果在完整响应生成后才返回。
- **超时。** 较长的响应可能触发默认的 120 秒超时。可用 `timeout=300` 提高上限。

## 完整示例

```python
"""
Claude Code 集成示例 —— 无需 API key。
演示一个多步骤的 agentic 工作流。
"""
from openprogram import agentic_function
from openprogram.providers import ClaudeCodeRuntime

runtime = ClaudeCodeRuntime(model="haiku")


@agentic_function
def brainstorm(topic):
    """围绕一个话题生成 3 个创意想法。"""
    return runtime.exec(content=[
        {"type": "text", "text": f"Generate exactly 3 creative ideas about: {topic}\nNumber them 1-3, one per line."},
    ])


@agentic_function
def evaluate(idea):
    """以 1-10 分评价一个想法的可行性，并给出简短理由。"""
    return runtime.exec(content=[
        {"type": "text", "text": f"Rate this idea's feasibility (1-10) and explain in one sentence:\n{idea}"},
    ])


@agentic_function
def ideate(topic):
    """头脑风暴想法，并逐一评估。"""
    ideas_text = brainstorm(topic=topic)
    print(f"💡 Ideas:\n{ideas_text}\n")

    lines = [l.strip() for l in ideas_text.split("\n") if l.strip() and l.strip()[0].isdigit()]
    for line in lines[:3]:
        rating = evaluate(idea=line)
        print(f"  📊 {rating}\n")

    return runtime.exec(content=[
        {"type": "text", "text": "Pick the best idea from the evaluation above and explain why in 2 sentences."},
    ])


if __name__ == "__main__":
    result = ideate(topic="improving developer productivity with AI")
    print(f"\n🏆 Best idea:\n{result}")
```

## 故障排查

| 错误 | 解决方案 |
|-------|----------|
| `FileNotFoundError: Claude Code CLI not found` | 安装：`npm install -g @anthropic-ai/claude-code` |
| `ConnectionError: Claude Code CLI not logged in` | 运行：`claude login` |
| `TimeoutError: Claude Code CLI timed out` | 提高超时：`ClaudeCodeRuntime(timeout=300)` |
| `RuntimeError: Claude Code CLI error` | 手动检查 `claude -p "test"` 是否正常工作 |
