# 故障排查

常见的坑。全新安装 / 升级的完整运维手册在
[`GETTING_STARTED.md`](../start/GETTING_STARTED.md) 中；本
页汇总反复出现的"它不工作"场景。

## "No provider available"

`openprogram providers` 列出已存的凭据；`openprogram providers discover` 扫描可采用的外部 CLI 登录态（Claude Code、Codex、Gemini CLI）。常见原因：

- 忘记执行 `openprogram providers login <provider>`（或对应外部 CLI 的登录）
- API key 设置在了与运行 worker 不同的 shell 中
- token 过期 —— 重新登录；`openprogram providers doctor` 可以诊断凭据的过期 / 刷新 / 冲突

## "command not found: openprogram"

pip 安装目录不在 PATH 中。两种方案：

```bash
# 直接调用模块
python3 -m openprogram <args>

# 或将 user-base 的 bin 加入 PATH（幂等）
echo 'export PATH="$(python3 -m site --user-base)/bin:$PATH"' >> ~/.zshrc
```

## Web UI 端口被占用

启动 worker 前设置以下环境变量之一：

```bash
export OPENPROGRAM_WEB_PORT=8101         # 前端（默认 18100）
export OPENPROGRAM_BACKEND_PORT=8102     # FastAPI（默认 18109）
```

或持久化该偏好：`openprogram ports --backend 8102 --frontend 8101`。

## 本地开发安装（多仓库）

如需在与 OpenProgram 并列的情况下开发
[GUI-Agent-Harness](https://github.com/Fzkuji/GUI-Agent-Harness)
/ [Research-Agent-Harness](https://github.com/Fzkuji/Research-Agent-Harness)：

```bash
pip install -e "$OPENPROGRAM_DIR"                   # 始终最先安装
pip install -e "$GUI_HARNESS_DIR"                   # 依赖 openprogram
pip install -e "$RESEARCH_HARNESS_DIR"
```

`openprogram/functions/agentics/{GUI,Research}-Agent-Harness`
是符号链接 —— 如果仓库移动了需要重新创建：

```bash
cd openprogram/functions/agentics
rm -f GUI-Agent-Harness  && ln -s "$GUI_HARNESS_DIR"      GUI-Agent-Harness
rm -f Research-Agent-Harness && ln -s "$RESEARCH_HARNESS_DIR" Research-Agent-Harness
```

`pip install -e` 写入的是绝对路径 —— 如果你重命名了某个父目录，请
从新位置重新运行它。

## worker 无法启动 / 启动在了错误的端口

`openprogram doctor` 会运行一次快速的端到端检查：Python/Node/git
工具链、技能和插件能否加载、provider 凭据、MCP server、磁盘缓存，
以及 worker 是否在 :18109 监听。`openprogram rescue` 在诊断之外
还会直接打印修复命令。在提 issue 之前先读一遍它们的输出。

## `import openprogram` 报 ModuleNotFoundError

该包没有安装在当前激活的 Python 中。要么运行安装程序
（克隆 OpenProgram + `./scripts/install.sh`），要么激活安装了它的
那个 venv。

## CI 显示"tests pass"但 Mac 上表现不同

有少数测试在裸 CI runner 上被显式跳过，
因为它们需要 `$HOME` 中配置好的 provider。跳过
列表就写在测试文件本身中 —— 搜索
`pytest.mark.skipif`。配有凭据的开发机器会看到
完整的测试套件。
