# Distill

OpenProgram 不再随安装包提供默认 `distill` skill 或 `/distill` 命令。
保留的 `read_conversation` 工具可以把历史会话提供给 Program，或者提供给用户、项目、插件安装的 skill。

可复用的产品工作流归 Programs。需要可复用模型指令的用户仍可自行创建或安装 AgentSkills 兼容的 `SKILL.md`。
