# grant tools

One tool for turning a research project into a funding application (`tasks/grant/`). It takes the project's Git repository as `paper_dir` (anything else raises `ValueError`), writes into `<out_dir>/<timestamp>/` with a trail (`run.json`, `run.md`), and returns JSON with `status` (`PASS`, `PASS_WITH_NOTES`, `FAIL`), `report`, `json` and `run`. The report is in Simplified Chinese.

Learned from Grantable, Instrumentl's AI writer, Granted AI, the NIH/NSF specific-aims helpers and the 国自然申请书 assistants: plan the aims first from what the project has shown, write each section in the funder's own structure, and keep every claim traceable to the project.

**The funder facts in `funders.json` (sections, length limits, required elements, budget categories, guide URLs) follow each funder's guide at the time of writing. Funders change them every cycle: check every limit against the current call (NSF PAPPG, NIH application guide and the funding opportunity, the ERC Work Programme, the year's NSFC 申请指南) before submitting.**

## Funders

| Key (aliases) | Language | Sections and limits | Required elements checked |
| --- | --- | --- | --- |
| `nsf` | English | Project Summary 4,600 characters; Project Description 15 pages; Data Management and Sharing Plan 2 pages | Summary headings Overview, Intellectual Merit, Broader Impacts; Description mentions Intellectual Merit, has a Broader Impacts heading and names every aim; DMP covers sharing (archiving is a note) |
| `nih_r01` (`nih`, `r01`) | English | Specific Aims 1 page; Research Strategy 12 pages | 2-4 aims, each named in both sections; Strategy headings Significance, Innovation, Approach; a hypothesis and alternative strategies are notes |
| `erc` (`erc_stg`, `erc_cog`, `erc_adg`) | English | Part B1 Extended Synopsis 5 pages; Part B2 Scientific Proposal 14 pages | Every objective named; B2 headings State-of-the-art and objectives, Methodology; ground-breaking and risk are notes |
| `nsfc` (`面上`), `nsfc_youth` (`青年`) | 简体中文 | 摘要 ≤400 字; 立项依据; 研究内容、研究目标及拟解决的关键科学问题; 研究方案及可行性分析; 特色与创新; 年度研究计划及预期研究结果; 研究基础 (正文 no fixed limit) | 研究意义、国内外现状; 小标题 研究内容 / 研究目标 / 关键科学问题, every 研究内容n named; 技术路线 and a 可行性 heading; 创新; 第1年 and 预期结果; 前期工作. 面上 48 months, 青年 36 |
| `generic` (`default`) | English | Summary 300 words; Aims 1 page; Background and Significance; Preliminary Results and Approach; Expected Outcomes and Impact | Every aim named in Aims |

Length units: `chars` (every character, spaces included), `words`, `zh_chars` (each CJK character or full-width punctuation mark is 1, each run of Latin letters or digits is 1), `pages` (estimated at about 500 English words or 800 汉字 per page). Lengths are measured on the text as submitted: without the draft marks, with each citation counted as `[1]`.

## grant_proposal

| Item | Contract |
| --- | --- |
| Purpose | A first draft of a grant proposal for one funder whose every number and citation comes from the project. |
| Input | `paper_dir`, `funder` (a key or alias above; unknown raises `ValueError`), `out_dir` (default `<paper_dir>/drafts/grant_proposal`). |
| Gather (code) | `workflow/project.md` Kind and Claim; `claims.md` (`Cn:` lines); `findings.md` (`## Fn: title` with Hypothesis / Run / Result / Verdict); `evidence.csv` rows; the best 3 kept (`keep`/`merge`) runs of `results.tsv` by `experiment.md` Metric and Direction, as R1-R3; the main `.tex` (rules.json `main_tex`, or the one top-level file with `\documentclass`) abstract and introduction when there is one; `literature.md` notes; the keys, titles and years of the top-level `.bib` files. Files still equal to their template count as empty. Nothing at all to write from raises `ValueError`. |
| Plan (role) | One `planner` call (`plan_aims`, schema `grant_plan`): title, goal, and the funder's range of aims (2-4), each with title, objective, gap, the claim ids it builds on, the evidence / finding / run ids that are its preliminary results, and start and end months within the funder's duration. Unknown ids, an aim without a claim (when claims exist) or months outside the duration raise `ValueError`. |
| Write (role) | One `writer` call per section (`write_section`, schema `grant_section`), in the funder's language, with the section's guidance, a length target, the required elements, the plan with aim labels (`Aim n`, `Objective n`, `研究内容n`), the project and the citable works. Paragraphs: heading, kind (`context`, `preliminary`, `proposed`), text, support ids (unknown ids raise `ValueError`). Citations only as `[@key]`. |
| Ground (code) | Every number with two or more digits or a decimal point must occur in the project files read (or be the funder's duration, a limit, or an aim's months); a sentence that breaks this, or cites a key the `.bib` lacks, is dropped and recorded. The plan's title, goal and aim texts are grounded the same way. A paragraph left empty goes; its heading moves to the next paragraph. A preliminary paragraph without support ids is a note. Claims no aim builds on and a missing `.bib` are notes. |
| Check (code) | Each section: length against its limit (over is an error); each required element (a regex on the headings or the text) at its level; every aim label at the section's `aims_named` level. A section whose sentences were all dropped is missing (error). |
| Output | `FAIL` on any error (a section missing, over its limit, or without a required element at error level); `PASS_WITH_NOTES` on dropped sentences or notes; else `PASS`. JSON also has `funder`, `proposal`, `sections`, `aims`, `dropped`. Files: `proposal.md` (title, funder, goal, aims, every section with **[Preliminary result]** / **[Proposed work]** marks, or **【前期结果】** / **【拟开展工作】** in Chinese, the timeline and the references cited), `sections/NN-<id>.md`, `timeline.md` (aims by year from their months), `budget_justification.md` (the funder's budget categories, each with TODO lines for amount, basis and the aims that need it), `grant.json`, `report.md` (aims with claims, evidence and gaps; each section's length against its limit and its problems; dropped sentences; the official guide URLs to check against). |
| Effects | Writes only the report folder; the project is never modified. |
