# 通过后端工具操作 OpenProgram

`framework` 工具发现并调用 OpenProgram 的已认证后端，可管理会话、项目、配置、文件、应用与执行，无需 Agent 点击 OpenProgram 界面。`resource` 工具操作持续存在的环境，包括网页、Terminal 和已安装应用。

## 发现业务操作

```python
framework(action="describe", operation="settings")
framework(action="invoke", operation="GET /api/settings")
```

使用发现结果中的准确 operation ID。HTTP 参数中，`path` 指定路径参数，`query` 指定查询参数，`body` 提供 JSON。二进制接口接受 `content_base64` 和 `content_type`。结果包含 HTTP 状态；失败不代表可以安全重发修改操作。发现接口支持 `arguments={"offset": 50}` 并返回 `next_offset`。

现有 WebSocket 业务命令也有后端请求／响应入口：

```python
framework(action="commands")
framework(action="command", operation="rename_session",
          arguments={"session_id": "EXACT_SESSION_ID", "title": "Research"})
```

参数以发现结果和原命令的校验规则为准。结果包含响应事件。长任务仍归 worker 管理，通过执行或会话接口读取进度与完成状态。文件操作保留原有 request ID、幂等键和版本校验。

## 不通过屏幕自动化操作窗口

```python
framework(action="interface")
framework(action="interface", operation="tabs.list",
          arguments={"window_id": "EXACT_WINDOW_ID", "arguments": []})
```

返回的目录声明标签、分组、分屏、侧栏、资源视图、外观、书签和原生 Desktop 命令及参数 schema。命令发送到一个准确的已认证窗口。历史、下载、浏览器导入和更新控制复用现有 Desktop 功能。普通浏览器窗口对原生专属操作返回不可用。目标不存在或不唯一时明确失败，丢失回执不会自动重发。

窗口显示操作需要连接中的界面；后端业务操作和已安装应用操作不需要前端页面。自动关闭文件标签前，必须先保存或放弃未保存的修改。

网页创建和关闭使用 `resource(provider="web", action="open"/"close")`。
网页原生视图命令还需要在 `arguments.web_session_id` 中提供本轮观察返回的控制会话，
继续检查原有 Page 独占控制、会话访问权限和已启用工具策略。

## 权限

工具保留正常审批并要求 owner 权限，不通过无约束的宿主机调用绕过当前 sandbox。认证初始化、传输回执和 Agent 审批回答不属于操作目录；Agent 不能借此批准自己的待审批操作。安装 Python 应用仍需明确授权信任，只有清单中 `agent: true` 的操作出现在资源操作 schema 中。

自动注册机制见[应用包](applications.zh.md)，Web 与 Terminal 行为见[桌面资源](../interfaces/desktop.zh.md)。
