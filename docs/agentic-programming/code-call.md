# @agentic_function 按固定顺序调用子函数

调用大模型（可选），按代码写死的顺序调用多个子函数。

## 适用场景

- 研究流程：调研 → 找 gap → 生成想法
- 论文流程：写初稿 → 审稿 → 修改
- 数据流程：采集 → 清洗 → 分析
- 任何步骤顺序固定的多步任务

## 设计要点

- 用 `@agentic_function` 装饰器
- 按固定顺序调用多个子 `@agentic_function`
- `exec()` 可选：不调（纯串联），或调多次（每次创建一个 exec 子节点）
- 子函数之间通过 Python 变量传递数据
- 一个函数可以调多次 `exec()`，也可以调任意多个其他 `@agentic_function`

## 示例：不调 exec，纯串联

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
    gaps = identify_gaps(survey=survey, runtime=runtime)
    ideas = generate_ideas(gaps=gaps, runtime=runtime)

    return {"survey": survey, "gaps": gaps, "ideas": ideas}
```

## 示例：调一次 exec 做总结

```python
@agentic_function
def research_pipeline(task: str, runtime: Runtime) -> str:
    """执行完整研究流程并总结结果。

    Args:
        task: 研究主题。
        runtime: LLM 运行时实例。

    Returns:
        整合后的研究总结。
    """
    survey = survey_topic(topic=task, runtime=runtime)
    gaps = identify_gaps(survey=survey, runtime=runtime)
    ideas = generate_ideas(gaps=gaps, runtime=runtime)

    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Survey:\n{survey}\n\n"
            f"Gaps:\n{gaps}\n\n"
            f"Ideas:\n{ideas}"
        )},
    ])
```

## Context Tree

```
research_pipeline
├── survey_topic       ← 第1步
├── identify_gaps      ← 第2步
└── generate_ideas     ← 第3步
```

## 步骤之间的数据传递

子函数之间通过 Python 变量传递，不需要大模型参与：

```python
survey = survey_topic(topic=task, runtime=runtime)
gaps = identify_gaps(survey=survey, runtime=runtime)
```

`survey_topic` 的返回值直接作为 `identify_gaps` 的输入参数。

## 步骤之间插入 Python 处理

```python
survey = survey_topic(topic=task, runtime=runtime)

# 中间插入普通 Python 处理
key_points = extract_key_points(survey)
filtered = [p for p in key_points if p["relevance"] > 0.5]

gaps = identify_gaps(survey="\n".join(filtered), runtime=runtime)
```

## 错误处理

```python
survey = survey_topic(topic=task, runtime=runtime)
if not survey or "error" in survey.lower():
    return {"error": "Survey failed", "survey": survey}

gaps = identify_gaps(survey=survey, runtime=runtime)
```

## 与"大模型选择调用"的区别

| | 固定顺序调用 | 大模型选择调用 |
|---|-----------|-------------|
| 谁决定调用顺序 | Python 代码 | 大模型 |
| 调用几个子函数 | 多个，全部执行 | 1个，选择执行 |
| 是否需要函数注册表 | 不需要 | 需要 |
| 灵活性 | 固定流程 | 根据任务变化 |
