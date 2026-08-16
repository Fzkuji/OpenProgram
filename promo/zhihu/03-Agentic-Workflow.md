# 候选标题

1. 代码门控说不动：OpenProgram 的 Agentic Workflow 怎么让 agent 变得可信
2. 模型答错就打回重来：我们在 agent harness 里做了硬性代码门控
3. 可信、还能自己进化：OpenProgram 的 Agentic Workflow 设计

---

上一篇讲了 `@agentic_function`：一个装饰器把 Python 函数变成 agent，docstring 是 prompt，类型注解是工具 schema。这一篇讲它撑起来的第二个机制——Agentic Workflow。目标只有两个字：可信。附带一个能力：自进化。

## 问题：agent 为什么不可信

现在的 agent 大多是一个循环：把工具塞给模型，模型想调什么调什么，循环跑到它自己觉得完事为止。这种结构下你没法保证任何事。模型可以跳过检查步骤，可以把"大概是个 feature request"这种模糊话当成结论，可以在第 40 轮忘掉第 3 轮定下的约束。你写再多"请务必"进 prompt，它也只是概率上更可能照做。

我们的判断是：流程该固定的部分不应该交给概率。Python 写流程，LLM 只做那些确实没法用代码写的判断。两者逐步交错，这就是 harness 的本职。

## 代码门控：验证失败就打回

Agentic Workflow 的第一个支柱是 code gate。`runtime.exec()` 可以带一个 `choices` 参数：

```python
kind = runtime.exec(ticket, choices=["bug", "feature", "question"])
```

这行代码的语义是：模型的回答必须能解析成三个选项之一，解析不出来就把失败原因发回去，让模型重新决策，直到过验证为止。返回值到你手里时一定是合法的，后面的 `if kind == "bug"` 是普通 Python 分支，百分之百执行。

这不是设想，下面是 README 里的真实 transcript：

```
llm  → "probably a feature request"
gate ✗ no parseable pick from ["bug", "feature", "question"]
llm  → {"call": "feature"}
gate ✓ → branch taken in Python
```

第一轮模型给了一句模糊的自然语言，门控拒收，把它打回去；第二轮模型给出了结构化的选择，通过，Python 侧接手。整个过程不需要你手写重试逻辑，也不需要在 prompt 里恳求模型"务必输出 JSON"。

关键在于：门控是代码，不是 prompt。prompt 里的约束模型可以不理，代码里的验证它绕不过去。一个必须执行的检查步骤写成 Python 语句，模型没有跳过它的选项——它连"跳过"这个动作都不存在。可信不是靠模型自觉，是靠结构保证。

在 prompt-based 约束和完全硬编码之间，这是第三条路：LLM 负责选择，代码负责这个选择必须合法、后续步骤必须发生。每个 `runtime.exec()` 都是执行 DAG 上的一个可重试节点，失败重试有记录，事后能看到模型在哪一步被打回过几次。调试 agent 不再靠翻几万 token 的对话记录。

## 自进化：agent 改自己的工具

第二个支柱更有意思：agent 可以给自己写新工具。

很多框架为此专门造了 `create_tool()`、`fix_tool()` 之类的 API。我们把这些全删了，理由是它们本来就多余：所谓"创建一个工具"，拆开看就是一次 LLM 调用加一次文件写入，agent 用普通的 read/edit 文件工具直接就能干。

OpenProgram 里的完整链路是：

1. agent 用普通文件工具，在 `openprogram/programs/agentic_functions/<name>/__init__.py` 写一个新的 `@agentic_function`（写法由内置的 `agentic-programming` skill 教给它）；
2. 一个 file watcher 检测到文件变化，热加载这个模块；
3. 下一轮对话，新函数已经出现在可调用的工具列表里。

没有注册文件，没有重启，没有任何专用机制。你在聊天里说一句"加一个函数，总结某个 tag 以来的 commit"，agent 写文件，watcher 加载，说完就能用。修 bug 同理：函数行为不对，agent 打开自己的源码改掉，热加载,下一轮生效。工具的增删改和普通代码维护是同一件事。

因为新工具也是 `@agentic_function`，它自动带上前面说的一切：docstring 即 prompt、类型注解即 schema、`choices` 门控、DAG 节点。agent 给自己造的工具和人手写的工具在机制上没有任何区别。

## /distill：把跑通的会话沉淀下来

自进化还有一条更省事的路径：不写新代码，把已经跑通的会话直接蒸馏成可复用的东西。

`/distill` 读取一个会话（当前的或者历史的），提取出目标、前置条件、步骤、决策点和踩过的坑，然后二选一落盘：执行时还需要判断的，写成 skill（一个 `SKILL.md`）；纯机械流程的，写成 `@agentic_function`。skill 目录同样是热加载的，写完立即生效——下次同类任务来的时候，模型自动匹配加载，或者你直接敲 `/<name>` 调用。

蒸馏出来的 skill 用错了还能修：说一句"那个 skill 不对，按这次学到的更新它"，agent 会改写原文件，保留验证过的步骤，替换被这次推翻的部分。记录一次、复用多次、用错了再精炼，工作流就是这样长出来的。

## deep_work：质量循环也是一个函数

把上面这些组合起来，可以搭出更重的东西。内置的 `deep_work` 就是一例：

```python
result = deep_work(
    task="Write a survey on context management in LLM agents.",
    level="phd",        # high_school → bachelor → master → phd → professor
    runtime=runtime,
)
```

它跑一个自主的 plan → execute → evaluate → revise 循环，直到产出过了指定的质量档位，状态持久化到磁盘，中断了能接着跑。注意它本身就是一个普通的 `@agentic_function`——循环用 Python 写死，每一步里的规划和评估交给 LLM。这正是 Agentic Workflow 的完整形态：流程可信，因为循环和退出条件是代码；结果有质量，因为判断交给了模型。

## 小结

可信的来源是代码门控：验证失败就打回，必经步骤写成 Python 语句，模型没有绕过的选项。自进化的来源是"工具即普通文件"：agent 用文件工具改自己的函数，watcher 热加载，会话经验用 /distill 沉淀成 skill。两件事共用同一个底座——`@agentic_function` 和执行 DAG。

项目开源（AGPL-3.0）：https://github.com/Fzkuji/OpenProgram

论文《LLM-as-Code: Agentic Programming for Agent Harness》已被 KDD 2026 Workshop on Agentic Software Engineering 接收：arXiv:2606.15874
