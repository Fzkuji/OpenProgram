# Workflow Control Primitives — 控制流原语设计

## 问题

当前 Planner 生成的代码需要手写验证、回溯、路由逻辑：

```python
# 手写版本：细节多、容易写错
files = agent("找文件")
if len(files.split('\n')) < 3:
    files = agent("扩大范围")
analysis = agent("分析：" + files)
if "缺少" in analysis:
    extra = agent("补充")
    analysis = analysis + extra
```

问题：
- Planner（LLM）不擅长写正确的条件判断（`len(...) < 3`）
- 验证和回溯逻辑重复，每个步骤都要写
- 意图不清晰：看代码才知道"这是在做验证回溯"

## 方案：提供控制流原语

给 Planner 一套**声明式的控制流工具函数**，让它组装而不是手写逻辑。

### 新增原语

#### 1. `validate_and_retry` — 验证和回溯

```python
files = validate_and_retry(
    action=lambda: agent("找 auth 相关文件"),
    check="文件数量 >= 3",  # LLM 判定的条件描述
    retry=lambda: agent("扩大范围，包括 oauth、openid 相关文件"),
    max_retries=2
)
```

**实现**：
- 执行 `action()`
- 用 `llm()` 判定结果是否满足 `check`（返回 YES/NO）
- 不满足 → 执行 `retry()`，重复最多 `max_retries` 次
- 都失败 → 返回最后一次的结果

#### 2. `route` — 路由选择

```python
strategy = route(
    question="这个任务适合什么迁移策略？",
    options=["直接迁移", "重构后迁移", "分阶段迁移"],
    context=analysis  # 可选：提供给 LLM 的上下文
)
# strategy = "直接迁移"（LLM 选择的选项）
```

**实现**：
- 用 `llm()` 从 `options` 中选一个
- 返回选中的字符串

#### 3. `conditional` — 条件分支

```python
result = conditional(
    condition="分析结果是否复杂？",
    context=analysis,
    if_true=lambda: agent("详细分析每个模块"),
    if_false=lambda: agent("给出简要结论")
)
```

**实现**：
- 用 `llm()` 判定 `condition`（返回 YES/NO）
- YES → 执行 `if_true()`
- NO → 执行 `if_false()`

#### 4. `repeat_until` — 循环直到满足

```python
complete_files = repeat_until(
    action=lambda: agent("找剩余文件"),
    condition="已找到所有相关文件",
    max_rounds=5
)
```

**实现**：
- 执行 `action()`
- 用 `llm()` 判定是否满足 `condition`
- 满足 → 返回结果
- 不满足 → 重复，最多 `max_rounds` 次

#### 5. `parallel` — 并行执行（可选，后期扩展）

```python
results = parallel([
    lambda: agent("分析前端代码"),
    lambda: agent("分析后端代码"),
    lambda: agent("分析数据库"),
])
# results = [前端分析结果, 后端分析结果, 数据库分析结果]
```

### Planner 生成的代码示例

**任务**："迁移 auth 模块到新客户端"

**生成的代码**：

```python
def workflow():
    # Step 1: 找文件，带验证和回溯
    files = validate_and_retry(
        action=lambda: agent("找 openprogram/auth/ 下所有相关文件"),
        check="文件数量 >= 5 且包含 oauth 相关",
        retry=lambda: agent("扩大范围，包括 openid、token 相关文件"),
        max_retries=2
    )
    
    # Step 2: 分析
    analysis = agent("分析这些文件的依赖关系和接口：" + files)
    
    # Step 3: 路由选择策略
    strategy = route(
        question="根据分析结果，选择迁移策略",
        options=["直接迁移", "重构后迁移", "分阶段迁移"],
        context=analysis
    )
    
    # Step 4: 条件分支执行
    plan = conditional(
        condition="选择的策略是否复杂（重构或分阶段）",
        context=strategy,
        if_true=lambda: agent("写详细的多阶段迁移计划：" + analysis),
        if_false=lambda: agent("写简要的直接迁移步骤：" + analysis)
    )
    
    return plan
```

**对比手写版本**：

```python
# 手写版本：38 行，充满判断逻辑
def workflow():
    files = agent("找 auth 相关文件")
    if len(files.split('\n')) < 5 or "oauth" not in files.lower():
        files = agent("扩大范围，包括 oauth")
        if len(files.split('\n')) < 5:
            files = agent("再次扩大，包括 openid、token")
    
    analysis = agent("分析：" + files)
    
    strategies = ["直接迁移", "重构后迁移", "分阶段迁移"]
    strategy_prompt = f"选择策略（{', '.join(strategies)}）：{analysis}"
    strategy = llm(strategy_prompt)
    
    if "重构" in strategy or "分阶段" in strategy:
        plan = agent("写详细计划：" + analysis)
    else:
        plan = agent("写简要步骤：" + analysis)
    
    return plan

# 控制流原语版本：20 行，意图清晰
def workflow():
    files = validate_and_retry(
        action=lambda: agent("找 openprogram/auth/ 下所有相关文件"),
        check="文件数量 >= 5 且包含 oauth 相关",
        retry=lambda: agent("扩大范围，包括 openid、token 相关文件"),
        max_retries=2
    )
    
    analysis = agent("分析这些文件的依赖关系和接口：" + files)
    
    strategy = route(
        question="根据分析结果，选择迁移策略",
        options=["直接迁移", "重构后迁移", "分阶段迁移"],
        context=analysis
    )
    
    plan = conditional(
        condition="选择的策略是否复杂（重构或分阶段）",
        context=strategy,
        if_true=lambda: agent("写详细的多阶段迁移计划：" + analysis),
        if_false=lambda: agent("写简要的直接迁移步骤：" + analysis)
    )
    
    return plan
```

## 文件结构变化

### 新增文件

```
openprogram/agentic_programming/control_flow/
├── __init__.py          # 导出所有控制流原语
├── validate.py          # validate_and_retry 实现
├── route.py             # route 实现
├── conditional.py       # conditional 实现
├── repeat.py            # repeat_until 实现
└── parallel.py          # parallel 实现（可选）
```

### 修改文件

1. **`openprogram/agentic_programming/__init__.py`**
   - 导出控制流原语：`from .control_flow import validate_and_retry, route, conditional, repeat_until`

2. **`openprogram/programs/functions/agentic/agentic_workflow/__init__.py`**
   - `_build_namespace()` 注入控制流原语（和 llm/agent/goal 一起）
   - 包上 checkpoint 包装器

3. **`openprogram/programs/functions/agentic/agentic_workflow/planner.py`**
   - 更新 system prompt，教 Planner 使用控制流原语
   - 添加工具函数的文档和示例

4. **`docs/capabilities/agentic-workflow.md` 和 `.zh.md`**
   - 添加控制流原语说明
   - 更新生成代码示例

5. **`docs/reference/design/runtime/self-programmed-agentic-workflow.html`**
   - 添加"控制流原语"章节
   - 更新注入环境的表格

### 新增测试

```
tests/unit/
├── test_control_flow_validate.py    # validate_and_retry 单元测试
├── test_control_flow_route.py       # route 单元测试
├── test_control_flow_conditional.py # conditional 单元测试
├── test_control_flow_repeat.py      # repeat_until 单元测试
└── test_workflow_with_control_flow.py  # 集成测试：workflow 使用控制流原语
```

## 实现顺序

### Phase 1：核心原语（validate、route、conditional）

1. **实现三个核心控制流函数**
   - `validate_and_retry`
   - `route`
   - `conditional`
   - 每个都写单元测试

2. **注入到 workflow 执行环境**
   - 修改 `_build_namespace()`
   - 包上 checkpoint 包装器
   - 写集成测试验证可调用

3. **更新 Planner prompt**
   - 添加控制流原语文档
   - 示例代码
   - 教它什么时候用哪个原语

4. **端到端测试**
   - 真实任务："迁移 auth 模块"
   - 验证 Planner 能生成使用控制流原语的代码
   - 验证代码能正确执行

### Phase 2：循环原语（repeat_until）

5. **实现 `repeat_until`**
   - 单元测试
   - 更新 Planner prompt
   - 端到端测试

### Phase 3（可选）：并行原语

6. **实现 `parallel`**
   - 需要线程池或进程池
   - checkpoint 并发写入处理
   - 更复杂，暂时可以不做

## 设计决策

### 为什么用 `lambda`？

```python
validate_and_retry(
    action=lambda: agent("..."),  # ← 为什么不直接 agent("...")
    retry=lambda: agent("...")
)
```

**原因**：延迟执行。

- 如果写 `action=agent("...")`，函数调用立即发生（在 `validate_and_retry` 执行之前）
- 写 `lambda: agent("...")`，只是传一个"如何执行"的函数，`validate_and_retry` 内部决定何时执行

### 为什么判定条件是字符串而不是函数？

```python
check="文件数量 >= 5"  # ← 字符串
# 而不是：
check=lambda r: len(r.split('\n')) >= 5  # ← 函数
```

**原因**：Planner（LLM）不擅长写正确的 Python 表达式。

- `len(r.split('\n')) >= 5` 容易写错（变量名、方法名、边界条件）
- `"文件数量 >= 5"` 是自然语言描述，LLM 擅长生成
- 框架内部用 `llm()` 把描述转成判定（返回 YES/NO）

### Checkpoint 如何记录？

每个控制流原语内部的 `llm()`/`agent()` 调用都正常记录：

```json
[
  {"call_index": 0, "fn": "agent", "prompt": "找文件", "result": "auth.py"},
  {"call_index": 1, "fn": "llm", "prompt": "判定：文件数量 >= 3？", "result": "NO"},
  {"call_index": 2, "fn": "agent", "prompt": "扩大范围", "result": "auth.py\noauth.py\nlogin.py"},
  {"call_index": 3, "fn": "llm", "prompt": "判定：文件数量 >= 3？", "result": "YES"}
]
```

`validate_and_retry` 本身不记录，它只是组合 `agent` 和 `llm` 调用。

### 如果 LLM 判定错误怎么办？

例如 `llm("文件数量 >= 5？")` 返回 YES，但实际只有 3 个文件。

**当前设计**：信任 LLM 判定。

**理由**：
- 精确判定（`len(...) >= 5`）需要 Planner 写正确的 Python 代码，它做不到
- LLM 判定虽然不精确，但"大概率对"（80-90% 准确率）
- 判定错误 → 后续步骤可能发现问题 → 整个 workflow 修订重跑

**未来优化**：允许混合模式
```python
check=lambda r: len(r.split('\n')) >= 5,  # 精确判定（用户手写）
# 或
check="文件数量 >= 5"  # LLM 判定（Planner 生成）
```

框架检测参数类型，`callable` 就直接调，`str` 就用 `llm()` 判定。

## 与现有机制的关系

| 机制 | 关系 |
|---|---|
| 三层原语（llm/agent/goal） | 控制流原语基于它们实现（内部调 llm/agent） |
| Checkpoint 机制 | 控制流原语透明：内部的 llm/agent 调用正常记录 |
| Planner 修订 | 不变：崩了还是 Planner 看错误改代码重跑 |
| `goal()` | `repeat_until` 是简化版 goal（只有循环，没有复杂判定） |

## 边界

**不做的事**：

1. **不做可视化编排**：不是拖拽式 workflow 编辑器，Planner 还是生成代码
2. **不做 DAG 执行引擎**：还是 `exec()` Python 代码，不是数据驱动的调度器
3. **不做运行时插入步骤**：还是只能修订重跑，不能运行中改代码

**这些是架构重构才能做的**，控制流原语只是"让 Planner 写出更好的代码"，不改执行模型。

## 总结

**问题**：Planner 手写验证、回溯、路由逻辑容易出错，代码冗长

**方案**：提供控制流原语（validate_and_retry、route、conditional、repeat_until），让 Planner 组装而不是手写

**收益**：
- 代码更短、意图更清晰
- Planner 不需要写 Python 细节（条件表达式、字符串处理）
- 统一的模式，容易调试和维护

**实现**：
- 新增 `openprogram/agentic_programming/control_flow/` 模块
- 注入到 workflow 执行环境
- 更新 Planner prompt
- 不改变执行模型（还是 exec Python 代码）
