# CLI

在脚本或其他程序里调用 OpenProgram，一条命令拿到一次回复。

## 单发

```bash
openprogram --print "总结这个目录里的 python 文件都做什么"
```

发送 prompt、打印回复、退出。不进入 TUI，也不依赖 worker 在跑（本次调用在进程内完成）。对话会写入会话存储，事后可在 Web UI 或 TUI 里翻看、续聊。

注意：`--print` 只接受参数里的字符串，不读 stdin。要把文件内容塞进 prompt，用 shell 替换：

```bash
openprogram --print "review 这段代码：$(cat main.py)"
```

## 续接指定会话

```bash
openprogram sessions list                 # 找会话 id
openprogram --resume <session-id> --print "接着上次的结论，下一步怎么做"
```

`--resume` 与 `--print` 搭配才生效。交互式续聊请在 TUI 里用 `/resume` 挑选会话（该参数目前在启动交互式 TUI 时被忽略），见[终端 TUI](tui.md)。

## 隔离环境

```bash
openprogram --profile ci --print "..."
```

`--profile <name>` 把配置、会话、凭据整体切换到 `~/.openprogram-<name>/`，脚本环境不污染日常环境。也可以用环境变量 `OPENPROGRAM_PROFILE` 设置。

## 其他脚本化入口

聊天之外的子命令都可直接脚本化，多数支持 `--json` 输出，例如：

```bash
openprogram sessions list
openprogram providers list --json
openprogram providers discover --json
openprogram status
openprogram programs run <name> --arg key=value   # 运行一个 agentic 程序
```

完整命令清单见 `openprogram -h`，每个子命令有自己的 `-h`。
