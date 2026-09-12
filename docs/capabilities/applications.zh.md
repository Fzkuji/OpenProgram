# 从新标签页打开应用

侧栏 **Applications（应用）** 页面支持从本地目录安装软件、查看来源和版本、打开、启停、隐藏启动按钮、从原来源更新，以及保留数据卸载。安装 Python 后端需要明确勾选信任。更新会填写安装表单，请审查来源后再提交。Tools、Workflows 和 harness 程序包仍在 **能力** 页面管理。

顶层 `openprogram apps` 命令管理软件；原 `openprogram programs apps` 命令继续兼容。软件注册保存到 `~/.openprogram/applications/catalog.json`，不再与 Program 来源混存。已有记录在首次访问时迁移，保留版本、启停与隐藏设置、卸载记录和实例数据；发生冲突时停止迁移，不覆盖任何一份记录。

安装后的应用会与文件、新建对话、浏览器、终端一起出现在新标签页。点击名称打开应用自己的界面，再次打开会选中同一个实例。关闭标签页不会取消后台操作。

应用界面使用标准 HTML、CSS 和 JavaScript，也可以提供 Python 业务操作。界面不必使用 OpenProgram 组件。前端依赖需要打包到本地，资源和 JavaScript 模块导入使用相对路径。应用运行在独立 opaque origin 的 sandbox iframe 中，不能读取宿主页面、直接调用宿主 API、加载远程脚本、嵌套其他页面或提交导航表单。资源 URL 只授权读取该应用版本的 UI 目录，不是宿主凭据。

## 安装与打开

在本地 OpenProgram worker 运行时执行：

```bash
openprogram apps install /absolute/path/to/application
openprogram apps list
```

Python 后端以当前操作系统用户执行受信代码。检查源码和依赖后，使用 `--trust` 授权运行：

```bash
openprogram apps install /absolute/path/to/application --trust
```

新标签页获得焦点时和每十秒刷新应用列表。项目级应用在打开时绑定当前选中的项目，之后切换对话项目不会改变已经打开的实例。没有当前项目时会复用唯一的已有绑定；首次使用或有多个绑定时提供项目选择，也可以选择新文件夹。全局应用使用当前 owner 的同一个实例；不同项目的实例独立保存业务数据。

仓库包含[计算器](https://github.com/Fzkuji/OpenProgram/tree/main/examples/applications/calculator)、[论文阅读器](https://github.com/Fzkuji/OpenProgram/tree/main/examples/applications/reader)和[文件分析](https://github.com/Fzkuji/OpenProgram/tree/main/examples/applications/file-analysis)样例。阅读器只在点击生成摘要时调用配置的模型，保存笔记不需要模型。

## 应用定义与接口

包根目录中的 `application.json` 定义稳定 `id`、`title`、`version`、`scope`（`global` 或 `project`）、`dataSchema`、`ui`、可选 `backend` 和 `operations`。完整 JSON 示例见[英文版](applications.md)。`ui.root` 相对包根目录，`ui.entry` 相对 UI 目录。Python 和私有配置应放在 UI 目录之外。

Python 后端的 `module:object` 入口导出操作字典。操作接收 `(input, context)`，同步或异步返回 JSON 值；输入输出使用 JSON Schema 校验。声明 `agent: true` 的操作可以由 Program 客户端调用。`backend.dependencies` 使用精确固定版本，例如 `package-name==1.2.3`。每个内容版本有自己的 Python 环境，同时使用已安装宿主框架及其依赖。安装时在独立进程中检查后端；列出应用不会导入代码。

支持的宿主能力是 `storage.app`、`model.invoke`、`files.project.read`。这些声明限制宿主 bridge，不限制受信 Python 代码的操作系统权限。

页面使用 `window.openprogramApp`：

- `load()` 返回 `{value, version}`，`save(value, version)` 只在版本仍匹配时保存，并发写入冲突后需要重新读取。
- `run(operation, input, requestKey)` 返回持久任务记录。重试相同请求时复用 request key，不同输入复用同一个 key 会被拒绝。
- `runs()` 列出最近任务；`status(id, after)` 返回状态、结果、错误、待回答问题和指定序号之后的事件。
- `cancel(id)` 取消任务；`answer(id, requestId, answer)` 回答当前问题。bridge 不能操作其他实例的任务。

Python 操作使用 `context.load()`、`context.save(value, expected_version=...)`、`context.progress(value)`、`context.ask(question)` 和 `context.read_file(relative_path)`。读取项目文件需要声明能力并绑定项目。声明模型能力的操作可直接使用现有 `llm()`、`agent()` 和 ambient Runtime，模型凭据留在后端。应用提问使用 `context.ask()`；要求预声明持久等待点的 Runtime 工作流交互仍保留原有要求。

Program 调用同一套操作：

```python
from openprogram.programs.application_client import run, status
job = run("local.notes", "summarize", {"text": "..."}, request_key="summary-1")
print(status(job["id"]))
```

CLI 对应 `programs apps run APP OPERATION --input JSON`，项目级应用增加 `--project ID`；查看和取消使用 `programs apps status RUN_ID`、`programs apps cancel RUN_ID`。

## 数据与生命周期

安装会复制代码并固定内容版本。修改源目录不改变已安装版本，使用 `install --replace` 更新；Python 应用还需要 `--trust`。已有任务继续使用旧代码，新版本激活后需要重新打开旧页面才能提交新操作。

业务数据与代码、对话分开保存，每个实例使用自己的 SQLite 状态，JSON 值上限为 4 MiB，单个事件上限为 1 MiB。执行状态使用现有 execution store；模型调用记录通过现有 session-node writer 保存到应用运行目录。

worker 停止会中断未完成的 Python 函数，重启后显示中断状态，不会自动恢复任意 Python 调用栈。应用应逐步保存进度，用户查看中断情况后明确启动另一次操作。当前版本拒绝改变数据 schema 或 scope 的升级，不提供自动迁移。

`openprogram apps uninstall APP` 移除菜单入口并取消活动操作，保留业务数据、旧代码及 schema/scope 兼容身份。重新安装兼容包可以恢复数据，不兼容的重新安装会在激活前被拒绝。owner API 的 `PATCH /api/applications/{id}` 支持 `enabled`、`hidden`、`display_title`；隐藏不取消任务，停用会取消。

独立部署、非 Python 后端、MCP Apps 兼容以及原生操作系统 GUI 窗口嵌入尚未实现。

项目应用在启动操作和读取项目文件前，根据绑定的项目 ID 重新确认当前位置。移动项目不改变应用实例和已保存数据；位置缺失、被其他目录替代或尚未确认时，拒绝启动新操作，已保存的应用数据仍可读取。
