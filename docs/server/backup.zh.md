# 备份与恢复

`openprogram backup` 把 profile 状态目录（记忆工作区、会话、配置、频道绑定）
打包成一个 `tar.gz`，之后可以恢复回来。

它解决的问题是：OpenProgram 关于你的一切都存在同一个隐藏目录里。
一次失败的迁移、一次对记忆工作区的实验、或者一条打错的 `rm`，
会把它们一次性全带走，而且这些东西都不在 git 里。

## 快速参考

```bash
openprogram backup create           # 立刻做一份快照
openprogram backup list             # 我手上有哪些备份
openprogram backup restore <name> --dry-run   # 恢复会覆盖什么
openprogram backup restore <name>             # 真的恢复
openprogram backup prune --keep 5   # 只保留最新的 5 份
```

归档文件放在 `~/.openprogram/backups/`（命名 profile 下是
`~/.openprogram-<profile>/backups/`），文件名为
`<profile>-<时间戳>.tar.gz`。POSIX 主机使用 `0600` 权限；Windows source
checkout 保留状态目录继承的 NTFS ACL，不改写 ACL 继承。归档先写入唯一的
临时文件，flush 后原子发布；POSIX 还会 fsync 所在目录。

## 备份范围

范围是一份白名单，不是"除了某些以外全都要"。
将来版本新增的缓存目录不会悄悄把你的归档撑大。

| 包含 | 内容 |
|---|---|
| `memory/` | 记忆工作区：`core.md`、主题、时间线、来源 |
| `sessions/`、`sessions.db`、`session_aliases.json` | 聊天历史及其索引 |
| `config.json`、`cli-config.json` | 你的配置 |
| `agents/`、`agents.json` | agent 定义 |
| `programs_meta.json`、`functions_meta.json`、`program-sources.json` | program 与 function 元数据 |
| `channels/`、`bindings.json` | 频道账号与会话绑定 |
| `skills/`、`skills.json` | skill 注册表 |
| `plugins/`、`marketplaces.json` | 已安装插件 |
| `mcp_servers.json`、`models/`、`commands/` | MCP 服务器、模型覆盖、自定义命令 |
| `owner.json`、`projects/`、`profiles/`、`worktrees.json`、`usage.db` | 归属、项目和账号元数据、worktree、用量历史 |

以下内容故意排除，因为下次启动会重新生成，留着只会让归档变大：

- `cache/`、`tool_results/`、`browser-states/`、`chrome-profile/`
- `trash/` 和 `shadow-git/`
- `logs/` 以及任何 `*.log`
- 锁文件、PID 文件、端口文件（`*.lock`、`*.pid`、`*.port`）
- web token，每次启动都会重新生成
- `.env.tmp`、`*.json.tmp` 等凭据 writer 临时文件
- 凭据账号的 `profiles/*/home/` 目录；profile 只允许 `metadata.json`，以及
  inventory 注册的 `.env` 和 AuthStore 路径进入归档
- 目录树里任何位置的 `node_modules/`
- 符号链接：跳过而不是跟随，否则一条指向状态目录外的链接会把无关的目录树拉进归档

## 凭据

默认情况下，归档会排除 `auth/`、`mcp_tokens/`、profile AuthStore 文件和
`.env` 文件、Channel
`credentials.json`；同时从混合配置中删除 `config.json[api_keys]`，以及
MCP server 的 env、header、bearer token 和 OAuth client secret 字段，
其余非秘密配置继续保留。

Web 运行期 token 和待处理的 Channel pairing code 永远不进入归档，
即使显式开启凭据备份也不例外。

```bash
openprogram backup create --include-credentials
```

这样会显式包含 inventory 中允许备份的全部长期凭据，并打印准确的明文凭据警告。
生成的文件必须采用与原始凭据相同的访问限制。归档内的 `backup-manifest.json`
只记录本次归档实际包含、redacted 或排除的凭据类别；全局 never-backup 规则
单独记录在 `credential_policy` 下，且不记录秘密值。
如果你只是要换一台机器，重新登录通常比拷贝归档更安全。

## 恢复

```bash
openprogram backup restore default-20260811-012458.tar.gz
```

在覆盖任何东西之前，会先做三件事：

1. **运行中进程检查。** 如果 worker 或 web 服务还开着，恢复会被拒绝：
   在活进程底下替换会话文件会把两边都搞坏。先用 `openprogram stop` 停掉。
2. **确认。** 命令会列出将要覆盖的内容并等你输入 `y`。脚本里可以用 `-y` 跳过。
3. **自动安全快照。** 先把当前状态备份成
   `<profile>-pre-restore-<时间戳>.tar.gz`，这样恢复错了也能再退回去。
   如果要恢复的归档是用 `--include-credentials` 创建的，这份快照会按同一份
   授权一并包含凭据：否则退回去的时候，恰好会丢掉这次恢复替换掉的那些秘密。
   发生时命令会明确提示。

用 `--dry-run` 只看覆盖清单，上面这些都不会发生：

```bash
openprogram backup restore default-20260811-012458.tar.gz --dry-run
```

恢复只替换归档里存在的条目，范围之外的状态（缓存、日志）原样不动。
如果混合文件中的已注册秘密字段缺失或被 redacted，恢复会保留本机现有值，
不会把 mask 写入，也不会删除本机秘密。

整个归档在任何内容可见之前先完整校验：路径包含关系、成员类型，以及每个
已注册秘密文件的 JSON 结构。symlink、hardlink 和路径穿越成员一律拒绝，
被拒绝的归档不会改动本机状态。通过校验后，每个文件都用 OpenProgram 其余
部分共用的 owner-only 原子写入发布，并且每次发布都记录 journal：因此被崩溃
或磁盘写满打断的恢复会被回滚，而不是停在改了一半的状态。下次执行恢复时
会自动先做这个回滚。POSIX 使用 descriptor-relative 路径遍历；Windows 的
Python 不提供这些 `dir_fd` 操作，因此 fallback 会在每次路径操作前验证包含
关系，并拒绝 symlink、junction 和其他 reparse point。

## 清理

备份不会自动删除。堆积起来之后：

```bash
openprogram backup prune --keep 5
```

保留最新的 5 份，删掉其余的，并打印释放了多少空间。
`--keep` 默认为 5，小于 1 的值会被拒绝。

## Profile

每个子命令只作用于当前 profile。在 `--profile alpha` 下，
备份从 `~/.openprogram-alpha/backups/` 读写，
恢复也绝不会跨到另一个 profile 的状态里。
