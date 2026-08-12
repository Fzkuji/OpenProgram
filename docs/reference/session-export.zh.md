# 会话导出

把一次对话写成文件留存、比对或发给别人：Markdown 用来阅读和复查，HTML 用来分享给没装 OpenProgram 的人。导出时会做脱敏。

## 命令行

```bash
openprogram sessions export <session-id>                       # ./<session-id>.md
openprogram sessions export <session-id> --format html
openprogram sessions export <session-id> --output ~/review.md
```

| 选项 | 含义 | 默认 |
|-----|------|------|
| `--format` | `md` 或 `html` | `md` |
| `--output` | 写到哪里 | `./<session-id>.<format>` |

会话 id 用 `openprogram sessions list` 查，或在 Web UI 侧栏看。

## Web UI

在侧栏右键点击一个会话（或用行上的 `⋯` 按钮），选**导出**再选格式，文件由浏览器下载。

同一份文件也可以直接从 `GET /api/sessions/{session_id}/export?format=md|html` 取。它是普通的 owner 鉴权端点，和 Web UI 其他请求用同一个会话 cookie，响应带 `Content-Disposition: attachment`。

## 文件里有什么

会话的一条分支：从分支头往回的对话链，和 Web UI 转录视图走的是同一条路径。每一轮带角色、本地时间戳和正文。发起工具调用的那一轮下面跟着它的调用：名称、成功或失败状态、参数、结果。

导出读的是会话当前活跃的头。有分叉的会话导出的是活跃分支，不是全部分支。

工具结果截断到 4000 字符，参数截断到 1000 字符，截断处就地标注（`… [+N chars truncated]`）。一次 2MB 的文件读取不应该淹没它周围的推理内容。对话正文本身不截断。

HTML 导出是单文件：样式内联，没有脚本，没有外部请求，通过 `prefers-color-scheme` 跟随读者系统的浅色深色设置。任何浏览器都能离线打开。

## 脱敏

导出里的每个字符串（对话正文、工具参数、工具结果）都会过一遍 provider 录制器用的那个密钥清除函数（`openprogram/providers/recording.py` 里的 `remove_secret_values`）。它清掉密钥类字段名（`api_key`、`authorization`、`token`、`password` 等）的值，也清掉自由文本里任何位置形如密钥的字符串：bearer token、`sk-` 开头的 key、URL 查询参数或 userinfo 里的凭证。清掉的值替换成 `[secret removed]`。

这是针对已知凭证形态的兜底，不是保证。格式特殊的密钥仍可能漏过去，所以导出文件发到公开场合前先自己读一遍。

## 相关

- [CLI 命令](cli.zh.md)：完整命令列表。
- [API](API.zh.md)：其余 HTTP 端点。
