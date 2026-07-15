# 纯 Python

## 何时使用

任务是纯确定性逻辑，不需要 LLM 推理。例如：
- 字数统计
- 文件读取 / 写入
- 数据格式转换
- 数学运算

## 设计要点

- **不要**使用 `@agentic_function` 装饰器
- **不要**调用 `runtime.exec()`
- 不需要 `runtime` 参数
- 使用标准的 Google 风格 docstring

## 示例

```python
def word_count(text: str) -> int:
    """Count the number of words in a text.

    Args:
        text: Input text.

    Returns:
        The word count.
    """
    return len(text.split())
```

```python
def extract_emails(text: str) -> list[str]:
    """Extract every email address from a text.

    Args:
        text: Input text.

    Returns:
        List of email addresses.
    """
    import re
    return re.findall(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
```

## 会话 DAG

纯 Python 函数不会在会话 DAG 上留下节点（除非用 `@traced` 装饰）。

如果你想把调用记录到 DAG 上，请加上 `@traced`：

```python
from openprogram.agentic_programming.function import traced

@traced
def word_count(text: str) -> int:
    """Count the number of words in a text."""
    return len(text.split())
```

该节点会记录函数名、绑定的参数（已剥离 `self`/`cls`/`runtime`/`callback`）以及返回值，`expose` 固定为 `'io'`。`async def` 函数同样受支持。

## 纯 Python 与 @agentic_function 对比

| 判断标准 | 纯 Python | @agentic_function |
|---------|----------|-------------------|
| 固定输入 → 固定输出 | ✓ | |
| 需要语义理解 | | ✓ |
| 需要自然语言生成 | | ✓ |
| 需要分类 / 判断 / 推理 | | ✓ |
| 有明确的算法 / 规则 | ✓ | |
