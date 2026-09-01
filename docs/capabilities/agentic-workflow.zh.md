# 自编程 Workflow

OpenProgram 可以创建、版本化、搜索和执行可复用的 Python Workflow package。任务需要稳定的多阶段结构、显式恢复、重复使用或可审查版本历史时，适合创建 Workflow；一个 Agent 可以直接完成的一次性任务不需要新建 Workflow。

## 当前四个入口

| 入口 | 职责 |
|---|---|
| `search_workflows(task)` | 只读搜索本地 catalog；不调用模型、不写文件、不执行候选。 |
| `create_workflow(task)` | 编写并静态校验一个新 package，然后发布首个 Git revision；不执行用户任务。 |
| `revise_workflow(workflow_id, request)` | 为指定 package 编写并静态校验新 revision；旧 revision 保持可用。 |
| `auto_workflow(task)` | 仅用户手动使用：搜索、选择复用或有依据的创建，然后执行固定 revision。 |

Chat Agent 可以直接调用搜索、创建、修订或具体 Workflow，但不能调用 `auto_workflow`；完整自动编排入口只在 Programs UI 提供给用户。

## 已发布 package 与一次运行不是一回事

已发布 Workflow 是带独立 Git 历史的多文件 Python package。每次执行会把一个不可变 revision 复制到会话仓库，并创建独立 `run_id`，记录 `state.json`、checkpoint、结果和 `project_ref.json`。

运行已发布 package 不会修改或重新发布它。执行失败时，run 进入 `failed`，保留原错误和 checkpoint；修改 package 必须显式调用 `revise_workflow`。

用户明确取消时，run 会把 `cancelled` 保存为终态。再次恢复该 `run_id` 只返回已保存的取消结果，不会重新执行。进程级 `KeyboardInterrupt` 则保存为 `interrupted`；后续调用可以复用该 run 保存的 artifact 和 checkpoint。这里的状态是 Workflow run 投影，不是 canonical Execution record。为恢复尝试分配新的 canonical `execution_id` 仍属于尚未完成的统一 Execution 接入。

历史 `code.py` run 仍可兼容恢复，但 legacy 单模块格式不再接受新 authoring，也不会出现在 Workflow 搜索结果中。

## 自己编写 package

当前 authoring 合同见[编写 Workflow package](workflows/authoring.md)，其中列出必需文件、metadata、入口签名、允许的 import、校验命令和当前接入限制。

```bash
openprogram workflows validate ./my_workflow
openprogram workflows validate ./my_workflow --json
```

静态校验不会 import package，也不会执行 `tests/test_workflow.py`。强制 sandbox 的行为测试门和人工 publish 命令目前还不是公开能力；CLI 会明确报告这一边界。

如果扩展是一个暴露多组 agentic function 的可安装仓库，而不是单个生成式 Workflow package，请使用另一套 [Harness 安装合同](installing-harnesses.md)。
