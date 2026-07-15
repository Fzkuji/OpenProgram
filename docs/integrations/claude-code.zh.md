# Claude Code

## 这是什么？

`ClaudeCodeRuntime` 让你**无需任何 API key** 即可使用 Agentic Programming。它使用你的 Claude 订阅的 OAuth token，直连 `api.anthropic.com`——token 从 [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) 的登录凭据（`~/.claude/.credentials.json`）解析而来，每次调用都重新读取，CLI 的 token 刷新自动生效。

只要你已安装并登录 `claude`，就可以直接上手。

## 前置条件

1. **安装 Claude Code CLI：**
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```

2. **登录：**
   ```bash
   claude auth login
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
    model="haiku",        # 模型名或家族别名（见下表）
    api_key=None,         # 一般不传；不传时每次调用从凭据池重新解析
    max_retries=2,        # API 层瞬态故障的重试次数
)
```

### 模型名称

`model` 接受家族别名或完整模型 id。别名展开为当前默认版本：

| 取值 | 展开为 |
|-------|-------------|
| `"sonnet"` | `claude-sonnet-4-6`（默认家族） |
| `"opus"` | `claude-opus-4-6` |
| `"haiku"` | `claude-haiku-4-5` |

更具体的 id（如 `claude-opus-4-5-20251101`）原样透传，由 Anthropic API 校验。

## 工作原理

在底层，`ClaudeCodeRuntime` 的流程是：

1. 从凭据池解析 Claude 订阅的 OAuth token（`sk-ant-oat` 前缀），或普通 Anthropic API key
2. 走标准的 Anthropic Messages 协议直连 `api.anthropic.com`，订阅 token 用 Bearer 认证 + Claude Code 身份 header
3. 把回复写回 session DAG

```
你的 Python 代码
    → @agentic_function 装饰器（记录一个 DAG 节点）
        → runtime.exec()（从 DAG 构建 prompt）
            → api.anthropic.com（订阅 OAuth 直连）
            ← 响应文本
        ← 回复作为 DAG 节点写回
    ← 返回值
```

全程没有子进程——它就是标准的 Anthropic 协议，只是凭据来自你的订阅。

## 限制

- **需要有效凭据。** 构造时校验凭据池里存在 Claude 订阅 OAuth token 或 Anthropic API key，否则抛 `ValueError`。
- **订阅 token 会过期**（约 8 小时）。runtime 每次调用重新解析，Claude Code CLI 侧的刷新自动生效；如果长时间没用过 `claude`，重新登录一次即可。

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
    print(f"Ideas:\n{ideas_text}\n")

    lines = [l.strip() for l in ideas_text.split("\n") if l.strip() and l.strip()[0].isdigit()]
    for line in lines[:3]:
        rating = evaluate(idea=line)
        print(f"  {rating}\n")

    return runtime.exec(content=[
        {"type": "text", "text": "Pick the best idea from the evaluation above and explain why in 2 sentences."},
    ])


if __name__ == "__main__":
    result = ideate(topic="improving developer productivity with AI")
    print(f"\nBest idea:\n{result}")
```

## 故障排查

| 错误 | 解决方案 |
|-------|----------|
| `ValueError: No Claude credential` | 运行 `claude auth login`（订阅），或在 Settings → Providers 添加 Anthropic API key |
| 认证相关的 4xx 错误 | token 过期或失效——重新 `claude auth login`，或用 `openprogram providers doctor` 诊断 |
| 模型 id 被 API 拒绝 | 别名（`sonnet`/`opus`/`haiku`）以外的 id 原样透传，检查拼写和版本号 |
