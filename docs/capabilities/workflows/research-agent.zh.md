# Research Agent

自主科研 agent：接一个研究选题，走完文献调研 → 想法生成 → 实验 → 写作 → 评审 → rebuttal / 展示的完整流程，产出可提交的论文。它不信任自己的输出——引用逐条对着 Crossref / OpenAlex / Semantic Scholar / arXiv 四个索引核验，论文里的数字要能追溯到实验的 `run_record.json`，评审可以换一个不同的模型来做（作者与审稿人不同模型，避免自评自）。

## 安装

```bash
openprogram programs install research
```

无额外依赖——harness 只用 OpenProgram 本身。可选项：PDF 解析（`load_paper` / wiki ingest 需要）另装 `pymupdf`；知识库联动另装 wiki harness（`openprogram programs install wiki`）。

## 怎么用

入口函数名为 **`research_agent`**，以工具形式（`as_tool=True`，toolset `research`）注册，聊天里直接描述研究任务即可触发，例如"Survey recent work on LLM uncertainty"。

命令行直接运行：

```bash
openprogram programs run research_agent -a task="Survey recent work on LLM uncertainty"
```

内部是两级控制：第一级由 LLM 选择进入哪个研究阶段（literature / idea / experiment / writing / review / rebuttal / presentation / theory / knowledge / project，阶段有依赖顺序，缺前置产出时会先补前置阶段）；第二级在阶段内由 LLM 依次挑选并运行该阶段的函数。共约 89 个函数、10 个阶段，每个函数是一个普通 Python 文件，docstring 就是 prompt，可直接编辑。

入口的几个隐藏参数（代码 / CLI 调用者可传）：

| 参数 | 说明 |
|---|---|
| `review_runtime` | 提供后，评审函数用另一个模型运行（跨模型评审） |
| `work_dir` | 项目工作目录 |
| `max_runtime_s` | 软性时间预算：到点后不再开新工作，正在跑的步骤跑完、正常收尾 |
| `stop_event` | 优雅停止信号（任何带 `is_set()` 的对象），当前步骤跑完后收尾 |

返回值是一个 dict：`task`、`success`、`summary`、`stages_completed`、`history`。

## 依赖注意

- 引用核验、无引用断言检查、citation-dump 检查等核查是纯 Python / 正则实现，不额外花 token；`integrity_gate` 用一次有界的 LLM 调用。
- 联网检索（arXiv / Semantic Scholar 等）需要网络可达；LaTeX 编译需要本机有 TeX 发行版。

源码与 README：`openprogram/functions/agentics/Research-Agent-Harness/`，上游仓库 [Fzkuji/Research-Agent-Harness](https://github.com/Fzkuji/Research-Agent-Harness)。
