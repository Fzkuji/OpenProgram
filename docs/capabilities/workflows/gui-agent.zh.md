# GUI Agent

给一句自然语言任务，它自主操作桌面：截图、识别界面组件、点击、输入、验证结果，循环直到任务完成或达到步数上限。适用于本机桌面，也可以通过 VM 接口操作远程虚拟机。感知层是 YOLO 组件检测（GPA-GUI-Detector）+ OCR（macOS 用 Apple Vision，Linux / Windows 用 EasyOCR）+ 模板匹配；动作层覆盖鼠标、键盘、剪贴板。在 OSWorld 基准的 Multi-Apps 子集上得分 79.8%（[结果](https://github.com/Fzkuji/GUI-Agent-Harness/blob/main/benchmarks/osworld/multi_apps.md)）。

## 可用性

每个受支持的 release 都会注册该 Program，并带上 Playwright Chromium 和 GPA detector 权重。不附带 PyTorch、OpenCV 或 EasyOCR，因此依赖这些库的桌面感知在打包产品里不可用。源码开发 checkout 仍可安装 harness 自己的依赖。开发者可以使用 editable GUI harness checkout，或替换 OCR/Browser backend 进行调试和后端开发。

## 怎么用

入口函数名为 **`gui_agent`**，以工具形式（`as_tool=True`，toolset `harness`）注册，聊天里直接描述桌面任务即可触发，例如"打开 Firefox 并访问 google.com"。

命令行直接运行：

```bash
openprogram programs run gui_agent -a task="Open Firefox and go to google.com"
```

Programs 卡片填写 `task` 和 `surface`。`desktop` 使用前台操作系统输入；`browser` 操作 OpenProgram 内置浏览器中的精确 Page。浏览器动作通过该 Page 的 DOM/CDP target 执行，不激活标签页、不置顶 OpenProgram 窗口，也不移动系统鼠标。ref 点击在 Page DOM 内触发，截图使用 Electron 的隐藏 Page capture，两者都不需要显示 Page。GUI Agent 会复用提交消息或 Function 表单的 OpenProgram 窗口中已有的 Page。该窗口没有 Page 时，它才会创建一个初始地址为 `https://www.google.com/` 的后台 Page，在同一次函数运行中继续执行，并在运行结束时只关闭这个由 agent 创建的 Page；当前选中的标签页保持不变。如果没有可用的来源桌面窗口，结果会是带接手说明的 `infeasible`，而不是运行时异常。如果桌面端拒绝清理，结果同样是 `infeasible`，并要求用户手动关闭残留的后台 Page。

输入完整的已注册函数表达式，例如 `gui_agent(task="检查这个 Page", surface="browser")`，会使用与 Programs 表单相同的 Function dispatcher，不会作为普通聊天消息保存或执行。Retry 会重新运行该精确 Function 节点及其保存的来源窗口/Page 身份，不会改用点击 Retry 时当前选中的 Page。

操作内置浏览器 Page：

```bash
openprogram programs run gui_agent -a task="检查并完成当前可见表单" -a surface=browser
```

参数（函数签名 `gui_agent(task, max_steps=None, app_name="desktop", surface="desktop", ...)`）：

| 参数 | 说明 |
|---|---|
| `task` | 要做什么（自然语言） |
| `max_steps` | 最大动作数。默认 150。`0` 或负数表示不封顶。 |
| `max_seconds` | 通过 OpenProgram Programs 或 Functions 调度时，它是两种 surface 共用的整个子进程截止时间。默认 300 秒；正数会覆盖默认值，`0` 或负数表示不限时。直接调用 Python bridge 时，browser 循环还会在内部执行该限制；desktop harness 没有单独的内部计时器。 |
| `app_name` | 用于组件记忆的应用名，如 `firefox`、`libreoffice_calc`，默认 `desktop` |
| `surface` | `desktop` 使用前台 OS/VM 输入，`browser` 使用精确的内置 Page。浏览器路径默认使用标准 Page backend；受信任调用方仍可显式传 `backend`。 |

桌面路径每一步执行 观察（一次截图 + 组件检测 + 状态识别）→ 验证上一步结果 → 规划一个动作 → 执行 → 构造下一轮反馈。第一步直接选择 `done` 时，还要单独验证当前屏幕是否已经满足任务。反馈只保留最近八个动作结果；同一个失败动作被第四次选择时会终止，不再无限重复。已学过的界面转换仍可复用，但学习改为显式操作，不再作为主循环之前的隐式步骤。做不完或需要人接手时，agent 会选 fail 停下并返回 `success=false`；`summary` 和 `handoff_instruction` 都保留接手说明。

桌面观察会包含当前前台应用和截图坐标范围。如果目标应用窗口被最小化或位于另一个 macOS Space，并且经过一次有界的 Window 菜单恢复后仍不可用，运行会以 infeasible 停止，并要求用户移动或取消最小化该窗口；它不会持续创建新窗口。

桌面坐标输入始终作用于当前前台 GUI，不是后台窗口接口。需要在不置顶 OpenProgram 或目标 Page 的情况下操作内置页面时，应使用 `surface=browser`。App 同时打开多个窗口时，自动 Page 只创建在提交该聊天消息或 Function 调用的窗口中。正常完成、失败、取消、超时以及子进程被强制终止都会尝试精确清理自动创建的 Page。清理被拒绝时不能报告成功：OpenProgram 会保留 Page 身份并执行一次有界重试；仍无法确认清理时返回手动关闭的接手说明。

桌面和内置浏览器运行共用终态字段：`status`（`succeeded`、`infeasible`、`failed` 或 `cancelled`）、`success`、`reason_code`、`summary` 和 `handoff_instruction`。成功与否由 runner 决定，不由 conclusion 模型决定。桌面结果还包含步骤历史和耗时；浏览器结果还包含 backend 和 WebSession 信息。

Function 卡片直接显示这个任务结果：验证成功显示 `Succeeded`，任务结束但未满足请求显示 `Failed`，接手说明要求用户操作时显示 `Needs takeover`。`Error` 表示运行时异常或不符合契约的 GUI 结果。内部 worker 的 completed 状态不会把失败的 GUI 结果显示成 `Completed`。

## 依赖注意

- 产品 runtime 不安装 PyTorch 或 EasyOCR。
- detector 模型缺失时，release capability probe 会拒绝该 artifact。
- 受支持的产品平台是 macOS 和 Linux。
- 运行前需要 runtime 配置好工作目录。工作流记录写入 OpenProgram 状态目录下的 `gui_harness/workflows/`，不再写入源码目录。

源码与 README：`openprogram/programs/applications/gui_harness/`，上游仓库 [Fzkuji/GUI-Agent-Harness](https://github.com/Fzkuji/GUI-Agent-Harness)。
