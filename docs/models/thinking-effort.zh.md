# thinking effort

各家 API 用不同参数控制推理深度（`effort` 字符串、`reasoning_effort`、token budget）。OpenProgram 把它们统一成一个档位滑块。

## 档位

框架统一定义 `off` + 六档：

```
minimal · low · medium · high · xhigh · max
```

每个模型只支持其中一个子集，界面按模型实际支持的档位显示。例如 Opus 4.8 是 low 到 max 五档，DeepSeek reasoner 不可调（永远全力推理），GitHub Copilot 线路不支持 thinking。档位数据来自各 provider 的声明（`provider.json` 的 `thinking` 块，含按模型的覆写）；端点自带能力信息的 provider——Anthropic 的 `/v1/models`、Codex 订阅模型端点——Fetch 时会刷新每个模型的档位。完全没有声明的 provider 自动退到 low / medium / high 三档。

## 默认值

默认档由各 provider 声明（`provider.json` 的 `default_effort`）：

| Provider 家族 | 默认档 |
|---|---|
| OpenAI（Responses 与 Completions）/ Codex / Azure OpenAI | `xhigh` |
| Anthropic / claude-code、Amazon Bedrock | `high` |
| Google / Gemini CLI（按 token budget 换算）、DeepSeek | `medium` |
| 无声明的 provider（三档兜底） | `medium` |

## 怎么改

由外到内三个层级，内层覆盖外层：

1. **agent 配置** `thinking_effort`：该 agent 的默认档（Web UI 的 agent 设置）。
2. **项目配置**：项目级默认值，覆盖 agent 默认。
3. **每轮请求**：聊天界面的档位选择器（Web UI 输入框旁），或 TUI 里的 `/effort` 命令；对当前会话逐轮生效。

选定的档位随会话持久化，provider 发请求前把框架档位翻译成各家 API 的实际参数。

映射规则与探测策略的完整记录见[设计笔记](../reference/design/providers/models/thinking-effort.md)。
