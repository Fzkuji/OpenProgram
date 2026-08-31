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

Programs 卡片填写 `task` 和 `surface`。`desktop` 使用操作系统输入；`browser` 操作 OpenProgram 内置浏览器中当前选定的精确 Page。

操作内置浏览器 Page：

```bash
openprogram programs run gui_agent -a task="检查并完成当前可见表单" -a surface=browser
```

参数（函数签名 `gui_agent(task, max_steps=None, app_name="desktop", surface="desktop", ...)`）：

| 参数 | 说明 |
|---|---|
| `task` | 要做什么（自然语言） |
| `max_steps` | 最大动作数。默认 150。`0` 或负数表示不封顶。 |
| `max_seconds` | 仅 web 路径的墙钟上限。默认不限时。`0` 或负数表示不封顶。桌面路径不用这个字段。 |
| `app_name` | 用于组件记忆的应用名，如 `firefox`、`libreoffice_calc`，默认 `desktop` |
| `surface` | `desktop` 使用 OS/VM 输入，`browser` 使用当前选定的内置 Page。浏览器路径默认使用标准 Page backend；受信任调用方仍可显式传 `backend`。 |

桌面路径每一步执行 观察（一次截图 + 组件检测 + 状态识别）→ 验证上一步结果 → 规划一个动作 → 执行 → 构造下一轮反馈。第一步直接选择 `done` 时，还要单独验证当前屏幕是否已经满足任务。反馈只保留最近八个动作结果；同一个失败动作被第四次选择时会终止，不再无限重复。已学过的界面转换仍可复用，但学习改为显式操作，不再作为主循环之前的隐式步骤。做不完或需要人接手时，agent 会选 fail 停下并返回 `success=false`；`summary` 和 `handoff_instruction` 都保留接手说明。

桌面观察会包含当前前台应用和截图坐标范围。如果目标应用窗口被最小化或位于另一个 macOS Space，并且经过一次有界的 Window 菜单恢复后仍不可用，运行会以 infeasible 停止，并要求用户移动或取消最小化该窗口；它不会持续创建新窗口。

桌面和内置浏览器运行共用终态字段：`status`（`succeeded`、`infeasible`、`failed` 或 `cancelled`）、`success`、`reason_code`、`summary` 和 `handoff_instruction`。成功与否由 runner 决定，不由 conclusion 模型决定。桌面结果还包含步骤历史和耗时；浏览器结果还包含 backend 和 WebSession 信息。

## 依赖注意

- 产品 runtime 不安装 PyTorch 或 EasyOCR。
- detector 模型缺失时，release capability probe 会拒绝该 artifact。
- 受支持的产品平台是 macOS 和 Linux。
- 运行前需要 runtime 配置好工作目录。工作流记录写入 OpenProgram 状态目录下的 `gui_harness/workflows/`，不再写入源码目录。

源码与 README：`openprogram/programs/applications/gui_harness/`，上游仓库 [Fzkuji/GUI-Agent-Harness](https://github.com/Fzkuji/GUI-Agent-Harness)。
