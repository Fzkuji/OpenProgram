# Skills

Skill 是给模型按需加载的领域知识：一个目录，里面一份 `SKILL.md`。这一页讲 skill 的格式、查找路径和管理命令，帮你给 agent 增加"会做某类事"的能力而不用写代码。

## 工作机制

1. 启动时扫描各 skill 目录下的 `<slug>/SKILL.md`，解析 front matter 里的 `name` + `description`。
2. 所有 skill 的 name 和一行 description 渲染成 system prompt 里的 skills 块（agentic runtime 的 `<available_skills>` 块还会带上每份 `SKILL.md` 的绝对路径）。
3. 模型判断当前任务是否匹配某个 skill 的 description；匹配时用 `read` 工具读取 `SKILL.md` 全文。全文不会自动注入。

skill 本身不执行任何东西。`SKILL.md` 旁边可以放脚本、参考文件、数据，由现有的 `bash` / `execute_code` 工具运行。每个已发现的 skill 还会被投射进 slash command 注册表，在聊天里输入 `/<name>` 即插入其正文。

## 格式

```markdown
---
name: my-skill
description: 一行说明什么时候该用它——模型靠这句话做匹配。
---

正文是自由 markdown：操作步骤、规则清单、示例……
```

front matter 是 `key: value` 形式的 YAML 子集；`name` 和 `description` 缺一不可，缺了该目录直接被跳过。

## 查找路径

system prompt 里的 skills 块按顺序探测两个位置，同名时先见者胜，因此**用户 skill 覆盖仓库 skill**：

1. `~/.openprogram/skills/`（用户级）
2. `<OpenProgram 仓库>/skills/`（项目级）

管理 CLI 与 slash command 投射则合并五个来源：bundled（随 OpenProgram 分发）、user（`~/.openprogram/skills/`）、project（`<cwd>/skills/`）、插件贡献（见 [Plugins](plugins.md)）、remote-cache（`~/.openprogram/cache/skills/`——`skills install` 下载落在这里）。

## 管理命令

```bash
openprogram skills list       # 按来源（bundled / user / project / plugin / remote-cache）列出已发现的 skill
openprogram skills doctor     # 扫描 skill 目录找问题
openprogram skills search <q>       # 在发现源里搜索（默认 ClawHub）
openprogram skills install <spec>   # 安装：slug（默认 ClawHub）、clawhub:<slug>、github:owner/repo
openprogram skills update --all     # 对比本地 SKILL.md 哈希与上游，重拉过期的（也可以只传一个名字）
openprogram skills remove <slug>    # 删除已安装 skill（限 project / user / remote-cache）
```

`install` 支持 `--source` 指定发现源 URL（`clawhub://`、GitHub 仓库或 JSON 索引）。
