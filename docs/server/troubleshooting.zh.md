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

受支持的 CLI/server 安装器会创建 `~/.local/bin/openprogram`。重新运行安装器，
然后确保该目录位于 `PATH`：

```bash
curl -fsSL https://openprogram.io/install | sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

## Web UI 端口被占用

启动 worker 前设置这个环境变量（API、WebSocket 和 web UI 共用一个端口）：

```bash
export OPENPROGRAM_WEB_PORT=8101         # 单端口（默认 18100）
```

或持久化该偏好：`openprogram ports --port 8101`。

## 本地开发

源码开发使用 `uv sync --locked --extra dev`。外部 harness 开发见
[安装 harness](../capabilities/installing-harnesses.zh.md)，它不是普通产品安装入口。

## worker 无法启动 / 启动在了错误的端口

`openprogram doctor` 会运行一次快速的端到端检查：Python/Node/git
工具链、技能和插件能否加载、provider 凭据、MCP server、磁盘缓存，
以及 worker 是否在 :18100 监听。在 Windows 上，它还会报告长路径配置和只读的
Defender 性能建议，不会修改注册表、Defender 排除项或文件 ACL。`openprogram rescue` 在诊断之外
还会直接打印修复命令。在提 issue 之前先读一遍它们的输出。

## `import openprogram` 报 ModuleNotFoundError

正式产品安装使用内置的私有 Python，不会把 OpenProgram 暴露给 shell 中
当前激活的 Python。源码开发时，先在 checkout 中运行 `uv sync --locked`，
再通过 `uv run --project /path/to/OpenProgram python ...` 执行脚本；也可以
先激活该 checkout 的 `.venv`。

## CI 显示"tests pass"但 Mac 上表现不同

有少数测试在裸 CI runner 上被显式跳过，
因为它们需要 `$HOME` 中配置好的 provider。跳过
列表就写在测试文件本身中 —— 搜索
`pytest.mark.skipif`。配有凭据的开发机器会看到
完整的测试套件。
