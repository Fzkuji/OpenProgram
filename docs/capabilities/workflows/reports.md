# Prepare weekly reports

The report suite consists of three independently versioned Workflow packages and
one coordinating `report` package. They must be installed in the Programs catalog;
this source checkout alone does not install them. The suite supports independent execution and composition through the `report` entry.
Native WeChat collection depends on accessible search and conversation controls;
unavailable controls return a recoverable state instead of a completed report.

| Entry | Purpose | External writes |
| --- | --- | --- |
| `weekly_report(task)` | Personal report drafts and explicit Feishu inspection or record updates | Only explicit standalone submission/update requests may write to Feishu |
| `group_weekly_report(task)` | Collect group reports, track missing members, and prepare a local summary | Never sends WeChat messages |
| `tencent_weekly_report(task)` | Approximately 100 Chinese characters of Tencent progress for a leader, intended for Friday afternoon | Local draft only |
| `report(task)` | Route one or several report requests and retain separate results | Prepares drafts; does not inherit permission to submit a personal report |

## Prepare several reports

Select `report` in Abilities and supply a JSON string as `task`:

```json
{
  "requests": {
    "personal": {
      "week": "2026-W37",
      "materials": [{"id": "p1", "week": "2026-W37", "text": "Experiments remain in progress; next week we will check the results."}]
    },
    "tencent": {
      "week": "2026-W37",
      "materials": [{"id": "t1", "week": "2026-W37", "text": "Tencent evaluation is still in progress. No final performance conclusion is available."}]
    }
  }
}
```

Materials are separate by audience. Personal and Tencent structured inputs require
matching ISO weeks and unique source IDs. Insufficient evidence returns a request
for input or review instead of inventing progress. Model-assisted semantic checks
can detect unsupported claims but are not a guarantee of factual correctness.

For a group request, provide `source` (`wechat` or explicitly `supplied`), `group`,
`week`, and the ordered `members` array. Supplied material additionally identifies
its `member`. WeChat collection requires an accessible, verified group interface;
if search or message metadata is unavailable, the Workflow returns a waiting state.
The coordinator requests noninteractive waiting, allowing other reports to finish.

Natural language can select a report type. Clearly identified single-audience
requests use code routing; mixed supplied content uses bounded model extraction
with verbatim source validation. Ambiguous requests ask for the intended audience.

## Continue incomplete work

The coordinator returns a status for every child and a `resume_task` object. Pass
that object back as the next `task`, optionally adding audience-specific updates:

```json
{
  "resume": "/absolute/path/to/checkpoint.json",
  "updates": {
    "tencent": {
      "materials": [{"id": "t2", "week": "2026-W37", "text": "Additional verified Tencent progress."}]
    }
  }
}
```

Completed children are not rerun. Updates cannot silently replace completed or
uncertain results. A crashed child marked `IN_PROGRESS` is not automatically
repeated; inspect its outcome before starting another request.

Local delivery failures retain prepared content for retry. Tencent model failures
retain generation and verification budgets across resumes. Checkpoints and retry
payloads contain private report content and should remain local.

## Friday afternoon Tencent report

The Tencent Workflow targets 80–120 non-whitespace characters, aiming for 100.
Its intended reporting window is Friday afternoon in `Asia/Shanghai`. It does not
create a recurring schedule or send to the leader. An exact execution time and
material source must be configured separately before enabling a scheduled run.
