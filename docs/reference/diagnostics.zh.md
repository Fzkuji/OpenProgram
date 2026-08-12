# 诊断包

`openprogram diagnostics` 把维护者处理故障报告所需的信息收集成一个 zip，其中的凭据已被移除，可以直接附在 issue 里，不用手工翻日志文件。

```bash
openprogram diagnostics                          # ./openprogram-diagnostics-<日期>.zip
openprogram diagnostics --output /tmp/report.zip  # 写到指定位置
```

命令会打印写入的文件清单，发送之前可以先看清楚包里有什么。

## 包内容

| 文件 | 内容 |
|------|------|
| `version.json` | OpenProgram 版本、Python 版本与解释器路径、平台与架构 |
| `config.json` | 你的 `config.json`，所有凭据形态的值已被替换 |
| `credentials.json` | 哪些 provider 有凭据、各有几个账号，只有名字和数量 |
| `environment.json` | `openprogram doctor` 的各项检查，外加 web 构建产物状态和 state/日志目录权限 |
| `logs/worker.log` | worker 日志最后 2000 行，已脱敏 |
| `logs/runtime.log` | runtime 日志最后 2000 行，已脱敏 |
| `logs/ink-startup.log` | 终端 UI 启动日志最后 2000 行，已脱敏 |
| `manifest.json` | 包内每个文件的来源和大小 |

本机不存在的日志不会出现在包里。

## 移除了什么

脱敏对进入包的每个文本文件生效，不只是配置文件，因为日志行和 traceback 里同样会带出凭据。分两层。

**按字段名。** 配置里凡是名字像凭据的键（`api_key`、`token`、`password`、`client_secret`、`authorization`、`cookie` 等，按子串匹配，所以 `openai_api_key`、`github_token` 也能命中），其值一律替换为 `[secret removed]`。命中的键会连同整棵子树一起替换，嵌套在下面的内容无法漏出。

**按值的形态。** 可识别的凭据格式在任何自由文本里出现都会被替换，包括 `sk-` 开头的 provider key、`Bearer` 和 `Basic` 认证头、GitHub 的 `ghp_` token、Slack 的 `xox` token、AWS access key id、Google 的 `AIza` key、JWT，以及嵌在 URL 里的凭据。日志行没有键名可查，靠的就是这一层。

凭据文件本身从不读取。`credentials.json` 是靠列出 auth 存储目录下的目录名生成的，所以已保存的 token 内容即使以脱敏形式也进不了包。

## 分享之前

脱敏做得比较彻底，但它无法判断日志里的某个文件路径、项目名或 prompt 片段对你是否敏感。附到公开 issue 之前，请自己打开 zip 看一遍。

## 相关

- [CLI 参考](cli.zh.md) — 全部子命令及其参数
- [配置参考](config.zh.md) — 脱敏后 `config.json` 里出现的那些键
