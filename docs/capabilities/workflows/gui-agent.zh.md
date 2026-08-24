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

参数（函数签名 `gui_agent(task, max_steps=15, app_name="desktop", ...)`）：

| 参数 | 说明 |
|---|---|
| `task` | 要做什么（自然语言） |
| `max_steps` | 放弃前的最大动作数，默认 15，可选 5 / 10 / 15 / 30 |
| `app_name` | 用于组件记忆的应用名，如 `firefox`、`libreoffice_calc`，默认 `desktop` |

每一步执行 观察（截图 + 组件检测 + 状态识别）→ 验证上一步结果 → 规划下一动作 → 执行 → 构造下一轮反馈，步与步之间传递结构化反馈，进度不依赖 LLM 上下文记忆。已学过的界面转换会作为捷径复用（组件记忆）。

## 依赖注意

- 产品 runtime 不安装 PyTorch 或 EasyOCR。
- detector 模型缺失时，release capability probe 会拒绝该 artifact。
- 受支持的产品平台是 macOS 和 Linux。
- 运行前需要 runtime 配置好工作目录（截图与运行记录写在那里）。

源码与 README：`openprogram/programs/applications/gui_harness/`，上游仓库 [Fzkuji/GUI-Agent-Harness](https://github.com/Fzkuji/GUI-Agent-Harness)。
