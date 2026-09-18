# Prepare weekly reports

The report suite consists of four independently versioned Workflow packages and
one coordinating `weekly_report` package. They must be installed in the Programs catalog;
this source checkout alone does not install them. The suite supports independent execution and composition through the `weekly_report` entry.
Native WeChat collection depends on accessible search and conversation controls;
unavailable controls return a recoverable state instead of a completed report.

| Entry | Purpose | External writes |
| --- | --- | --- |
| `personal_weekly_report(task)` | Personal Feishu drafts and explicit Feishu inspection or record updates | Only explicit submission/update requests may write to Feishu |
| `personal_chat_weekly_report(task)` | Owner's three-section WeChat-group copy (`1.本周进展` / `2.开会讨论要点` / `3.下周计划`) | Local draft only; never sends WeChat |
| `group_weekly_report(task)` | Collect group reports, track missing members, and prepare a local summary | Never sends WeChat messages |
| `tencent_weekly_report(task)` | Approximately 100 Chinese characters of Tencent progress for a leader, intended for Friday afternoon | Local draft only |
| `weekly_report(task)` | An agent selects the requested report Workflows and forwards the complete request | Preserves the requested operation; child write restrictions apply |

## Prepare a Tencent report from existing records

Select `tencent_weekly_report` in Abilities and enter `Prepare this week's Tencent
weekly_report`. The Workflow uses the current ISO week in Asia/Shanghai. It first reuses
original current-week Tencent evidence in the workspace's `reports` directory,
then considers other report sources and dated OpenProgram memory. Explicit
materials take precedence. Records from other weeks, uncertain month/year-only
dates, untrusted memory and explicit test records are excluded. Other audiences
require source selection with exact quotes; they are not automatically treated
as Tencent work. Discovery is bounded, so unavailable or insufficient sources
still require clarification. Optional `report_roots` selects up to five source
directories.

Successful Tencent and `weekly_report` calls return the report body directly. Sources,
character count and model-call information remain in local draft files. For
programmatic composition, pass `"result_format": "structured"` in the JSON task;
this returns the status, artifact paths and recovery object. The router forwards explicitly supplied child options unchanged. Generation and verification use low reasoning effort,
a 180-second per-call limit and preserved retry budgets. Model failures do not
produce a success draft.

## Prepare several reports

Select `weekly_report` in Abilities and supply a JSON string as `task`:

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

For a natural-language group request, a read-only agent first looks for missing
source-group identity and ordered members in memory and saved local meeting
records. It preserves explicit user scope and asks for clarification when sources
do not establish one group and roster. This step cannot send messages, write
files, operate WeChat, or call another report.

For a structured group request, provide `source` (`wechat` or explicitly `supplied`), `group`,
`week`, and the ordered `members` array. Supplied material additionally identifies
its `member`. WeChat collection requires an accessible, verified group interface;
versions without accessible message rows can use bounded window OCR. Captured
pages and their original OCR rows are saved as local source evidence. Unknown
authors, uncertain dates, clipped messages, and low-confidence boundaries remain
unverified. If native search focus cannot be proved, open the requested group
and resume; the Workflow still performs collection and summarization. A window
that does not permit capture returns `WINDOW_CAPTURE_UNAVAILABLE`. No screen or
WeChat settings are changed.
Each child retains its own interaction and recovery behavior. Group request
parsing uses schema-validated model output, permits prompt fallback for providers
without verified native schema support, and uses the Runtime's existing bounded
repair retry. Exhausted model-format failures return `WAITING_MODEL` with the
original request for resumption, rather than claiming that user input is missing.
Routing, group request parsing, and personal report model calls inherit the
Runtime timeout (`OPENPROGRAM_EXEC_TIMEOUT_S`); they impose no separate 90-second
limit. External record updates are not automatically repeated after an uncertain
failure.

Natural-language requests are interpreted by a routing agent. It selects one,
two, three, or all four destinations according to meaning and negation, then code
passes the entire original request unchanged to each selected Workflow. It does
not split on punctuation, extract materials, force drafts, or assign a common
week. A generic personal progress draft without Feishu language is
`personal_chat`, not the Feishu form. Unclear destinations require clarification.
Single results are returned verbatim; multiple results are labeled by audience.

## Continue incomplete work

The router owns no shared checkpoint. Continue through the relevant child using
that child's recovery instructions. Old coordinator resume payloads are rejected
with guidance to use the child entry. Explicit `requests` JSON addresses children
directly and preserves their options without routing-model inference.

Local delivery failures retain prepared content for retry. Tencent model failures
retain generation and verification budgets across resumes. Checkpoints and retry
payloads contain private report content and should remain local.

## Friday afternoon Tencent report

The Tencent Workflow targets 80–120 non-whitespace characters, aiming for 100.
Its intended reporting window is Friday afternoon in `Asia/Shanghai`. It does not
create a recurring schedule or send to the leader. An exact execution time and
material source must be configured separately before enabling a scheduled run.

## Source organization

The five independent report Workflow packages are grouped under `openprogram/programs/workflow/weekly_report/`: `personal_weekly_report` (Feishu), `personal_chat_weekly_report` (owner's WeChat-group copy), `group_weekly_report` (group collection), `tencent_weekly_report` (Tencent), and `weekly_report` (routing). Shared internal helpers live in `workflow/_reports/`. The coordinator is named `weekly_report`. Configured output paths are unchanged. A category directory organizes sources; it is not an additional Workflow.

Personal field generation uses a read-only Agent to retrieve the requested week from memory and local notes. A JSON Schema validates complete fields or sparse edits, with prompt fallback and the existing format-repair attempt before any write. An unresolved model failure retains the original request; an uncertain remote write is not retried.

Structured Agents count normal tool rounds against `max_iterations`, not the failed-request allowance. A completed tool round preserves the remaining retry allowance; format repairs and failed requests still share it. Reaching the iteration limit stops execution without replaying completed tools.

Read-only memory search, grep, get and browse use the normal safe-tool policy; explicit deny/ask rules and `memory.read` authority still apply. Personal operation selection and evidence verification, plus every Tencent JSON-producing stage, declare output schemas and reuse Runtime format repair. A format failure returns model-recovery state rather than requesting missing user input. Tencent request interpretation inherits the deployment timeout.
