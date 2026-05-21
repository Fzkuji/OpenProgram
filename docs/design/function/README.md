# Function 设计总览

本目录描述 Agentic Programming 中函数的设计规范。

## 目录结构

```
function/
├── README.md               ← 本文件
├── pure_python.md          ← 不调用大模型的普通 Python 函数
├── agentic_function.md     ← 调用大模型的 @agentic_function
└── function_calling/       ← 函数调用函数的两种情况
    ├── code_call.md        ← 代码决定调用顺序（固定流程）
    └── llm_call.md         ← 大模型决定调用哪个函数（tool_use 原生 API）
```

## 核心规则

1. **一个 `@agentic_function` 可以调用多次 `runtime.exec()`**（每次创建一个 exec 子节点）
2. **一个函数可以调用任意多个其他 `@agentic_function`**
3. **Docstring 是 prompt**，content 只放数据
4. **让 LLM 动态选函数用 `runtime.exec(tools=[fn1, fn2, ...])`**，provider 原生 tool_use 协议处理分发

## 相关文件

- 原子工具：`openprogram/functions/tools/<name>/`（bash / read / write / edit / glob / grep 等）
- 完整样例：`openprogram/functions/agentics/llm_call_example/__init__.py`
- 框架核心：`openprogram/agentic_programming/function.py`（`@agentic_function` 装饰器、`.spec` 属性）
- 框架核心：`openprogram/agentic_programming/runtime.py`（`Runtime.exec()` 的 `tools=` 参数）
