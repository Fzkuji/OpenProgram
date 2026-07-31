# 自我更新：OpenProgram 一边改自己一边保持可用

*设计记录，2026-08-01。状态：方向已定；阶段 1 是当前实践，阶段 2–3 未实现。*

## 1. 问题

当 OpenProgram 成为用户唯一的 agent 工具时，它也就是开发 OpenProgram 的工具。
此后每次改动都带着同一个风险：为聊天服务的进程，正是代码被编辑的进程。
一次坏改动绝不能让用户失去可用实例——因为可用实例是修复坏改动的唯一途径。

今天的实际风险比表面更大：CLI 是**指向工作仓库的 pip editable 安装**
（`pip show openprogram` → `Location: …/OpenProgram`）。agent 在仓库里改文件，
改的就是运行中服务器的代码路径。Python 只在 import 时读文件，所以进程在重启前
是安全的——但*下一次*重启会加载磁盘上任何半成品状态。

## 2. 现有的保障

| 特性 | 效果 |
|---|---|
| 状态全部落盘（`~/.openprogram/`：会话、DAG、检查点、shadow git） | 重启只丢一次 WebSocket 连接；前端自动重连，历史完整。 |
| 前端是静态导出（`web/out/`），由 worker 服务 | UI 改动只需 `next build`；零停机，不重启。 |
| `openprogram restart` | Python 侧改动几秒生效，轮与轮之间无感。 |
| `--profile <name>` | 配置/会话/日志改道 `~/.openprogram-<name>/`——第二个实例可以带完全隔离的状态运行。 |
| `openprogram worker install` | 登录服务 + 崩溃自动重启：坏构建启动即死时监督进程会不断重试（也意味着坏构建会崩溃循环——见 §5）。 |
| `openprogram doctor` | 现成的健康检查，可复用为重启前的闸门。 |

缺的两块：没有任何机制在重启前确认代码真的能跑，也没有机制在重启后确认
正在服务的确实是新代码。

## 3. 参考项目

**OpenClaw**（`openclaw update`，最接近的同类——常驻 gateway 自我更新）：

- 源码检出的更新是固定步骤链：git fetch/checkout → 装依赖 → build →
  `doctor` → 重启 gateway → **验证重启后的服务报告的版本号是预期新版**。
  任何一步失败都带结构化原因（`doctor-failed` 等）中止，且*不*重启。
- gateway 从不在自己进程里跑完更新：起一个 detached helper、自己退出，
  由 helper 在进程树外走正常 CLI 更新路径。
- npm 安装走 **staged install**：先装进临时 prefix、就地校验包树，
  通过了才换入真实位置。
- 降级需确认（老代码可能读不懂新配置）。

**Claude Code / VS Code**：下载与激活分离——新版本落到独立目录，当前会话
继续跑旧版，下次启动才切换。下载失败零影响。

**Home Assistant / Jupyter**：完全不做热切换——状态全落盘、重启便宜、
客户端自动重连。这就是 OpenProgram 的现状模型。

**nginx / Caddy**：传 listener fd 的真零停机二进制切换。对单用户工具过重，
不采用。

## 4. 设计

两条规则，加一个强制执行它们的命令。

### 4.1 规则一——服务检出与编辑检出分离

开发在 `git worktree` 里进行；运行实例服务稳定检出。agent 在 worktree 里
改代码、跑测试（`pytest`、`npm run check`）、构建。要真机验证 Python 改动时，
从 worktree 用另一个端口加 `--profile dev` 起第二个实例，状态隔离。
验证通过后才合入服务检出。

仅这一条（阶段 1）就已保证任何时刻有可用实例：最坏情况是坏掉一个*候选*，
而不是坏掉*服务器*。

### 4.2 规则二——重启必须过闸门

代码更新用 `openprogram upgrade` 取代裸 `restart`。步骤链仿 OpenClaw：

1. **预检**——服务检出干净吗？目标 ref 存在吗？降级 → 询问确认。
2. **fetch + checkout** 目标 ref（默认 `origin/main`）。
3. **依赖**——依赖文件变了才 `pip install -e .`；lockfile 变了才
   `cd web && npm ci`。
4. **构建**——`next build`。
5. **doctor 闸门**——`openprogram doctor` 外加冷启动探测：用
   `--profile upgrade-probe` 在临时端口拉起新代码，等 `/healthz` 通过后
   杀掉。import 错误、配置 schema 不兼容、端口绑定失败都在这里拦住，
   真实实例尚未被碰。
6. **重启**真实实例。
7. **验证**——轮询 `/healthz` 直到它报告预期的 git SHA（该端点新增
   `version`/`sha` 字段）。不符或超时 → **回滚**：`git checkout <上一个
   sha>` + 重启 + 再验证。

每一步记录结构化结果；第 6 步之前的任何失败都不影响运行中的实例。

### 4.3 脱离进程树执行

和 OpenClaw 一样，升级不能在被替换的进程里跑到底。`openprogram upgrade`
本身是普通 CLI 进程（CLI 与 worker 本来就分离）；当它*从聊天轮内*被触发时，
工具调用以 detached 方式（`start_new_session`）拉起它，worker 自身重启
不会杀掉进行中的升级。helper 把进度写进 sentinel 文件，新 worker 启动时
读取——升级后的第一个聊天轮就能汇报"已升级到 <sha>"或"已回滚：<原因>"。

### 4.4 扩展点（先留口子，不先实现）

刻意保持开放，后续需求接入时不用重塑命令本身：

- **渠道（channel）**。`upgrade` 不硬编码任何 ref：目标一律经一个
  `channel → target ref` 解析函数得出，今天只有一个内置渠道
  （`stable → origin/main`）。以后加 `beta → origin/beta` 或
  `dev → <worktree 分支>` 只是加一条表项加一个持久化的
  `update.channel` 配置键（OpenClaw 的模型），不是重写。CLI 形态从
  第一天就预留 `upgrade --channel <name>` 和 `upgrade status`。
- **分发方式**。步骤链抽象为*解析目标 → 物化 → 校验 → 激活*四个动词。
  今天"物化"就是 `git checkout`；将来 pip/npm 包安装实现同样四个动词
  （其"物化"即 OpenClaw 式 staged install）。第 5–7 步（探测、重启、
  验证）本来就与分发方式无关。
- **更新源**。目标解析接受 remote 名，默认 `origin`——fork 或私有镜像
  只是一个配置值。

### 4.5 明确不做的

- 热加载 / fd 交接——重启只要几秒且会话不丢。
- 后台自动更新——由用户（或其 agent）主动发起。

## 5. 失败模式

| 失败 | 拦截点 | 结果 |
|---|---|---|
| 新代码语法/import 错误 | 第 5 步冷启动探测 | 不重启；实例不受影响 |
| schema/配置不兼容 | 第 5 步 doctor | 不重启 |
| 新代码能启动但版本不对（构建陈旧、检出错误） | 第 7 步验证 | 自动回滚 |
| 回滚本身失败 | 第 7 步再验证 | sentinel 记录；worker 监督进程尽量保住旧进程；文档化的逃生通道是手动 `git checkout` + `openprogram restart` |
| `worker install` 监督下的崩溃循环 | 监督进程重启计数 | 监督进程应退避并钉住上一个 sha（阶段 3） |
| 有轮正在跑时请求升级 | 第 1 步预检 | 等空闲，或要求 `--force` |

## 6. 分期

- **阶段 1（现在，纯纪律）**：worktree 开发 + `--profile` 第二实例真机
  验证 + 合入后 `restart`。现有参数已支持；本文将其定为必须的实践。
- **阶段 2**：实现 `openprogram upgrade`，含 §4.2 步骤链但不含自动回滚：
  预检、检出、依赖、构建、探测、重启、验证。`/healthz` 增加 `sha` 字段。
  内部按 §4.4 的扩展点组织（渠道表、四动词步骤链），即便当下只有一个
  渠道、一种分发方式。
- **阶段 3**：验证失败自动回滚、sentinel 结果注入升级后首个聊天轮、
  监督进程退避钉版。
