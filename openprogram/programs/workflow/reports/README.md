# Report Workflows

Each child is an independent Git-backed Workflow package. Folder grouping does not change its public entry point.

| Folder / entry point | Purpose |
| --- | --- |
| `weekly_report` | Personal weekly report; prepare, submit or update through its existing interface. |
| `group_weekly_report` | Collect group reports and prepare a combined draft; never send WeChat messages. |
| `tencent_weekly_report` | Prepare an approximately 100-character current-week Tencent report; never send. |
| `report` | Route requests to the three independent Workflows and preserve their separate recovery state. |

Shared implementation lives in `../_reports/`. Generated drafts and checkpoints remain in the configured reports output directory, not here.
