# 纯 Python 函数

## 适用场景

任务是纯确定性逻辑，不需要 LLM 推理。例如：
- 字数统计
- 文件读写
- 数据格式转换
- 数学计算

## 设计要点

- **不用** `@agentic_function` 装饰器
- **不用** `runtime.exec()`
- **不需要** `runtime` 参数
- 用标准 Google-style docstring

## 示例

```python
def word_count(text: str) -> int:
    """统计文本中的单词数量。

    Args:
        text: 输入文本。

    Returns:
        单词数量。
    """
    return len(text.split())
```

```python
def extract_emails(text: str) -> list[str]:
    """从文本中提取所有邮箱地址。

    Args:
        text: 输入文本。

    Returns:
        邮箱地址列表。
    """
    import re
    return re.findall(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
```

## Context Tree

纯 Python 函数不出现在 context tree 中（除非加了 `@traced` 装饰器）。

如果希望在 execution tree 中看到调用记录，可以加 `@traced`：

```python
from openprogram.agentic_programming.function import traced

@traced
def word_count(text: str) -> int:
    """统计文本中的单词数量。"""
    return len(text.split())
```

## 何时用纯 Python，何时用 @agentic_function

| 判断标准 | 纯 Python | @agentic_function |
|---------|----------|-------------------|
| 输入确定 → 输出确定 | ✓ | |
| 需要理解语义 | | ✓ |
| 需要生成自然语言 | | ✓ |
| 需要分类/判断/推理 | | ✓ |
| 有明确的算法/规则 | ✓ | |
