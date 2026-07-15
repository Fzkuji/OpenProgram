# Plugins

插件是打包好的扩展：一个带 manifest 的 pip / npm / git / 本地包，装进宿主后向 OpenProgram 贡献 commands、skills、MCP server、providers、hooks、agents、侧边栏项、web 页面等。这一页讲插件怎么装、怎么管，以及插件长什么样。

## 安装与管理

```bash
openprogram plugins list                 # 已安装插件
openprogram plugins search <query>       # 在配置的 marketplace 里搜索
openprogram plugins install pip <package>       # 来源四选一：pip / npm / git / path
openprogram plugins install git <url> --ref v1.2
openprogram plugins install path /abs/path/to/plugin
openprogram plugins update               # 从 pip / npm 重装升级
openprogram plugins enable <name>
openprogram plugins disable <name>
openprogram plugins uninstall <name>
```

安装位置统一在 `~/.openprogram/plugins/`（npm 包在其 `node_modules/` 下）；信任等级持久化在 `~/.openprogram/plugin-trust.json`。Web UI 通过插件路由暴露同一套管理 API。

## 插件是什么形态

一个目录（或已安装的包），带三种 manifest 之一，解析优先级从高到低：

1. `plugin.json`（顶级文件，claude-code / hermes 风格）
2. `pyproject.toml` 里的 `[tool.openprogram.plugin]`
3. `package.json` 里的 `"openprogram"` 字段（opencode 风格）

manifest 字段包括 `name`、`version`、`description`、`trust`（community / verified）、`entrypoints`、`sidebar`、`options`、`requires`。`requires` 声明插件间依赖：加载器按拓扑序决定 enable 顺序，缺依赖时报 `missing dependencies: ...`。

加载时导入 `entrypoints` 指向的模块（pip entry point 的 `module:obj`、或 manifest 里的 `python` / `module` 键），插件由此向贡献注册表暴露自己的 commands / skills / mcpServers / providers / hooks / agents / sidebar / web 入口，宿主各子系统从注册表读取并接合。
