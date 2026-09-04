# GUI Agent

给它一个自然语言任务。根控制器重复选择一个有界能力：`computer_use` 操作本机桌面，`browser_use` 操作 OpenProgram 后台 Page，`vm_use` 操作已配置的远程虚拟机。每次能力调用的输入和完整结果都会追加到下一轮模型决策的上下文。模型通过提交终态结束任务；动作数和时间限制只作为安全边界。

本机与 VM 感知使用 YOLO 组件检测（GPA-GUI-Detector）、OCR（macOS 用 Apple Vision，Linux / Windows 用 EasyOCR）和模板匹配。动作层覆盖鼠标、键盘和剪贴板。浏览器操作使用 Page 的 DOM/CDP target，不使用桌面坐标。

## 可用性

每个受支持的 release 都会注册该 Program，并带上 Playwright Chromium 和 GPA detector 权重。不附带 PyTorch、OpenCV 或 EasyOCR，因此依赖这些库的桌面感知在打包产品里不可用。源码开发 checkout 仍可安装 harness 自己的依赖。开发者可以使用 editable GUI harness checkout，或替换 OCR/Browser backend 进行调试和后端开发。

## 怎么用

公开入口函数名为 **`gui_agent`**，以工具形式（`as_tool=True`，toolset `harness`）注册。公开输入只有 `task`。规划函数和三个 `*_use` 函数仍会生成可追踪的子函数节点，但不会分别注册成公开工具。

命令行直接运行：

```bash
openprogram programs run gui_agent -a task="Open Firefox and go to google.com"
```

Programs 卡片只填写 `task`，不要求用户选择 surface。控制器每一轮都会读取原始任务、此前能力函数的准确输入和完整输出，以及当前能力可用状态，再决定下一步调用。

任务适合由当前内置浏览器 Page 完成时，仍使用同一个入口：

```bash
openprogram programs run gui_agent -a task="在不置顶窗口的情况下检查并完成当前内置浏览器表单"
```

受信任调用方还可以提供隐藏的控制器设置：`max_steps` 是动作安全上限，默认 150；`max_seconds` 是可选的总耗时安全上限；`app_name` 选择组件记忆；`backend` 指定已有 Page backend；`vm_url` 启用 `vm_use`。旧 `surface` 字段只作为兼容性偏好继续接受，不会把运行固定在某种能力上，也不出现在公开函数 schema 中。

控制流程如下：

1. `plan_next_capability` 接收任务、当前可用状态和完整的有序能力调用历史。
2. 它选择 `computer_use`、`browser_use`、`vm_use`，或者提交一个终态。
3. `call_capability` 绑定由控制器管理的 runtime 设置，并只调用本轮选中的函数。
4. 该函数的准确输入和完整输出追加到 history，因此下一轮决策可以直接看到。
5. 提交的终态要经过校验；没有完成证据的 success 会被记录并继续规划。

`computer_use` 和 `vm_use` 每次执行一个现有 Harness step：观察当前目标、在存在前序反馈时验证结果、规划一个动作、执行，并返回完整 step 和下一轮反馈。`browser_use` 每次执行一个有界的后台 Page 子任务，然后把控制权返回根循环。屏幕读取任务不走单独的预先分流。

实现统一使用 OpenProgram 的高层 agentic programming 调用。`plan_next_capability`、桌面规划、验证和结论通过当前 Runtime 上下文调用 `llm()`；浏览器 Page 动作循环通过带动作工具和单次有界迭代的 `agent()` 执行。GUI workflow 代码不直接调用 `Runtime.exec`。根控制器不再嵌套一层 `goal()`，因为能力历史、终态提交、证据验证、超时、取消和无进展判断已经由根控制器负责；再加一层 goal 控制器会重复这些判断。

`vm_use` 需要兼容 OSWorld 的 HTTP endpoint。截图通过 `GET /screenshot` 获取，输入命令通过 `POST /execute` 发送。Harness 进程内的 VM 目标切换会串行执行。无论调用成功还是抛错，下一种能力执行前都会恢复原来的输入目标和截图 backend。endpoint 中的凭据和 query 值不会写入规划器的可用状态上下文。

桌面观察会包含当前前台应用和截图坐标范围。如果目标应用窗口被最小化或位于另一个 macOS Space，并且经过一次有界的 Window 菜单恢复后仍不可用，运行会以 infeasible 停止，并要求用户移动或取消最小化该窗口；它不会持续创建新窗口。

桌面坐标输入始终作用于当前前台 GUI，不是后台窗口接口。浏览器动作在选中的 Page 后台执行，不激活标签页、不置顶 OpenProgram 窗口，也不移动系统鼠标。根据已记录的结果，控制器可以在不同能力之间切换。

所有运行共用终态字段：`status`（`succeeded`、`infeasible` 或 `failed`）、`success`、`reason_code`、`summary` 和 `handoff_instruction`。成功与否由 runner 决定，不由 conclusion 模型决定。只有 `succeeded` 的 `success` 为 true；infeasible 和 failed 一律返回 `success=false`。infeasible 还保留 blocker、标记和用户接手说明。结果同时包含有序能力调用历史和耗时。

`max_seconds` 会在每次模型或能力调用前检查，并在调用返回后再次检查。超过截止时间才返回的终态提交会被拒绝，并统一为超时失败。Provider 取消采用协作式机制，因此已经发出的 provider 请求可能略晚于配置的总耗时边界才返回，但该迟到结果不会把任务变成成功。

Function 卡片直接显示这个任务结果：验证成功显示 `Succeeded`，任务结束但未满足请求显示 `Failed`，接手说明要求用户操作时显示 `Needs takeover`。`Error` 表示运行时异常或不符合契约的 GUI 结果。内部 worker 的 completed 状态不会把失败的 GUI 结果显示成 `Completed`。

## 依赖注意

- 产品 runtime 不安装 PyTorch 或 EasyOCR。
- detector 模型缺失时，release capability probe 会拒绝该 artifact。
- 受支持的产品平台是 macOS 和 Linux。
- 运行前需要 runtime 配置好工作目录。工作流记录写入 OpenProgram 状态目录下的 `gui_harness/workflows/`，不再写入源码目录。

源码与 README：`openprogram/programs/applications/gui_harness/`，上游仓库 [Fzkuji/GUI-Agent-Harness](https://github.com/Fzkuji/GUI-Agent-Harness)。
