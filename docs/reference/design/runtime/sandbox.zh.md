# 执行身份、权限与沙箱

沙箱现与上游 Authority、Permission 合同一起维护在唯一权威设计中：[执行权限与沙箱设计](sandbox-architecture.html)。

权威文档覆盖：

- 固定的 `owner`、`paired` 和 `mcp_browser` 权限档；
- permission mode、项目规则、交互审批和非交互拒绝；
- macOS Seatbelt、Linux bubblewrap 与 Windows WSL2-to-bubblewrap 执行边界；
- `auto` 能力检测，包括 Windows 可选 WSL2 后端缺失时的原生未沙箱回退，以及显式
  选择 `workspace-write` 时的 fail-closed 行为；
- bypass 与 sandbox escalation 语义；
- 请求来源、框架对照、失败行为、实现证据和已知缺口。

本页只作为产品文档和旧链接的稳定入口保留，不是第二份沙箱设计正文。
