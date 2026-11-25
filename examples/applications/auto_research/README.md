# auto_research

The entry for a research project. Its tasks are declared as data in `registry.py`, and the entry, the status view and the app all read that table. Each task does one thing: it checks one set of artifacts, or it runs one workflow. Tasks are not grouped into larger steps such as "revise" or "submit". They do not depend on each other: any task runs on its own and in any order, and it checks only what it reads. A new need becomes a new row when it arises; nothing is built ahead of it. The phase column is only a label the app groups rows by.

| Item | Contract |
| --- | --- |
| Purpose | Check one task's own inputs and run that task's workflow. |
| Input | `paper_dir`: the project's Git repository. `task`: `status` (default) or a task name from the registry. `section`, `draft_dir`, `baseline`, `scope`, `phases`, `direction`, `reader_level`, `purpose`, `our_work`, `preset` and `reviewer_model` go only to the tasks that declare them. A required input that is empty, an unknown task or a missing repository raises `ValueError`. |
| Output | For a task: JSON with `task`, `status` (`PASS`, `BLOCKED`, or the workflow's status), `problems`, `blocked_by` (the first gate that failed), `created`, `next`, `run`, and `check` (the workflow's result). For `status`: every task as the app shows it (`name`, `title`, `phase`, `gates`, `artifacts`, `inputs`, `required`, `workflow`, `state`, `blocked_by`, `problems`, `next`, `done`, `last_runs`) and the plan cards. |
| Effects | A task run copies its missing artifacts from `templates/` and never overwrites them; `status` writes nothing. For each task run it writes a run record to `drafts/auto_research/<timestamp>/` and the latest result to `drafts/auto_research/state.json`, keyed by task, or by task and section, with its inputs and the commit it ran at. `status` only reads them. It runs each gate once, and skips gates that read an input (the plan card) or the network (the code release). A task whose artifacts are missing or still the template, and that never ran, reads `not_started` and is not judged. It never edits the paper. |
| Model calls | None of its own. Workers are plain code that fill every argument of the child workflow, and model calls happen only inside the child. citation_check runs with `investigate=off`, which makes no model calls. field_survey runs research_survey, whose roles make 50 to 90 calls for a field. idea_generation makes three calls (ideator, assessor, and a judge that referees every pair of ideas for an Elo ranking) and searches the public APIs of arXiv, Semantic Scholar and OpenAlex. paper_review makes one reviewer call, on `reviewer_model` when one is named. figures makes none and runs the paper's plotting scripts. The code_release gate reads GitHub's public API (`GH_TOKEN` raises the rate limit). |
| Failure and cancellation | A failed gate is `BLOCKED`, not an exception. Workflow errors propagate. Rerunning is safe. |
| Tests | `tests/test_workflow.py`: the registry, every gate, independent tasks, the argument hand-off, the status view with last runs and the run record. `tests/test_trail.py`: the run record. `tests/<task>/`: each task workflow's own suite (`citations`, `figures`, `ideas`, `review`, `revision`, `survey`). |
| Usage | `openprogram programs run auto_research -a paper_dir=<repo> -a task=paper_check -a section="Workflow Evolution (RQ2)"` |

## Tasks

| Task | Title | Phase | Gates | Workflow | Passes when |
| --- | --- | --- | --- | --- | --- |
| survey | 调研材料 | research | survey | none | venue.md, rules.json and literature.md are filled; surveyed keys are in the .bib |
| field_survey | 领域综述 | research | none | research_survey (`tasks/survey/`) | the survey of `direction`, within the tracks of `preset` when one is named (`llm_knowledge`), reaches `min_papers` core papers with no audit issue left; the page and records go to `workflow/surveys/<direction>/` |
| idea_generation | 想法生成 | research | gaps | idea_generation (`tasks/ideas/`) | at least one proposed idea is novel against the records its search found; the ideas come from the open directions of the field surveys (of `direction` when given) and the works in literature.md, and go to `drafts/idea_generation/` |
| idea | 想法与研究问题 | research | idea | none | every claim names its RQ and the author approved them |
| experiments | 实验证据 | research | experiments | none | every number has an existing source, and every claim has evidence |
| plan | 章节计划 | writing | plan | none | the section's plan card is filled and approved |
| paper_check | 论文检查 | writing | plan, when a section is given | paper_revision_check (`tasks/revision/`) | the check of the section, or of the whole paper, has no FAIL |
| settings_check | 实验设置核对 | writing (method, analysis, benchmark) | none | settings_check (`tasks/reproduce/`) | every setting the paper reports matches the code's defaults and configs, in `workflow/settings.csv` |
| result_numbers | 结果数字核对 | writing | none | result_numbers (`tasks/resultnumbers/`) | every uncited number in the paper's tables and prose matches a kept run or evidence source at its rounding precision; stale, misattributed (other run, metric or split) and wrong derived claims ("26% lower", "outperforms") flagged with edits.json |
| missing_baselines | 漏掉的更强基线 | writing (method, benchmark) | none | missing_baselines (`tasks/lineage/`) | no uncited work since `since` (default three years back) reports a stronger number on the paper's dataset, metric and setting than the paper; works citing the dataset paper and the baselines found through OpenAlex and Semantic Scholar, numbers and settings quoted from their full text |
| baseline_numbers | 基线数字核对 | writing (method, benchmark) | none | baseline_numbers (`tasks/reading/`) | every number the paper copies from a cited work matches a quoted passage of that work, in `workflow/sota.csv` |
| related_by_paragraph | 逐段文献推荐 | writing | none | related_by_paragraph (`tasks/reading/`) | each paragraph's uncited related works from Semantic Scholar search and recommendations (for `arxiv_id`), with candidates.bib; the import_annotations operation reads Zotero highlights |
| skim_test | 速读测试 | writing | none | skim_test (`tasks/reading/`) | a reader of only the title, abstract, captions and first sentences gets the main claim |
| lean_check | 定理形式化 | writing | none | lean_check (`tasks/outreach/`) | every theorem mapped to a declaration in `workflow/lean/` builds without sorry; the lean_draft operation drafts a statement |
| lean_prove | 定理证明 | writing | none | lean_prove (`tasks/outreach/`) | each `sorry` of workflow/lean/ (or `theorem`) replaced by a proof Lean accepts: automation first, then a prover over `attempts` rounds fed Lean's errors; statement unchanged, standard axioms only, the project still compiles |
| citation_check | 参考文献核查 | writing | none | citation_check (`tasks/citations/`) | every entry of every .bib matches a published record |
| figures | 论文图表 | writing | figures | paper_figures (`tasks/figures/`) | every script named by `figure_scripts` in rules.json follows the figure rules and runs; the figures are redrawn, and the reports go to `drafts/paper_figures/` |
| writing_style | 写作风格 | writing | none | writing_style (`tasks/style/`) | workflow/style.md learned from `own_papers` (statistics in code, motivation and contribution habits by a model), and exact edits toward it and Writing in the Sciences in edits.json |
| paper_translate | 论文翻译 | writing | none | paper_translate (`tasks/style/`) | the whole LaTeX source translated to `language` with commands, math, labels and citations protected and checked, terms kept to workflow/glossary.md, compiled with xelatex |
| method_diagram | 方法示意图 | writing | none | method_diagram (`tasks/diagrams/`) | the method section's components and data flow (labels found in the text) laid out in code as editable TikZ, compiled and checked by eye |
| caption_check | 图表标题检查 | writing | none | caption_check (`tasks/diagrams/`) | every figure and table has a label, is referenced, and is understandable from its caption alone (rewrites use only numbers in the paper or results); plotting scripts checked for font size, vector output and axis labels |
| proofread | 论文校对 | writing | none | paper_proofread (`tasks/proofread/`) | no math or number error in any section; claims without a citation get candidate works, language and notation fixes come as exact edits (`edits.json`), and three titles and two abstracts are proposed; reports go to `drafts/paper_proofread/` |
| citation_graph | 引文图谱 | writing | none | citation_graph (`tasks/library/`) | no reference is retracted (OpenAlex, Crossref); works cited by at least `min_shared` of your references but not by you are listed, with graph.html; reports go to `drafts/citation_graph/` |
| peer_feedback | 投稿前找人审读 | writing | none | peer_feedback (`tasks/feedback/`) | candidates who wrote the closest works, scored by topic, seniority and activity, without conflicts (recent co-authors, same institution or parent, `exclude`, declined before); `count` request drafts in workflow/feedback/ citing each person's real work, never sent; workflow/feedback.csv tracks them |
| paper_review | 论文评审 | writing | none | paper_review (`tasks/review/`) | a panel of `reviewers` (default 3, each with its own focus) and an area chair review the paper, or `draft_dir`; the panel scores 7 or more with no fatal weakness; reviews go to `drafts/paper_review/`, and each says which weaknesses of the last one are resolved |
| code_wiki | 代码 Wiki | release | none | code_wiki (`tasks/codewiki/`) | a static site of module pages and a call graph for `code_dir`, every method subsection, labelled equation and algorithm mapped to the functions implementing it with checked file:line and paper quotes |
| code_origin | 代码来源与许可证核查 | release | none | code_origin (`tasks/codeorigin/`) | every file of `code_dir` known to Software Heritage or matching a verified origin is credited in THIRD_PARTY_NOTICES.md, adapted files found by winnowing fingerprints, and no file's license conflicts with the repository's |
| software_citation | 软件引用文件 | release | none | software_citation (`tasks/citationcff/`) | valid CITATION.cff (CFF 1.2.0, preferred citation the paper), .zenodo.json and codemeta.json for `code_dir`, ORCID iDs only on a checked match, an archive DOI found on Zenodo or DataCite that resolves and is cited by the paper |
| code_release | 代码发布 | release | code | none | `code.repo` in rules.json is empty, or it is public, has a README, is linked from the paper and has only allowed contributors |
| deliverables | 海报与报告材料 | release | deliverables | none | every `{name, path}` in `deliverables` of rules.json exists |
| watch | 新工作跟踪 | research | none | related_work_watch (`tasks/watch/`) | no new paper judged to overlap a claim is left without a `handled` note in watch.csv; queries live in watch.md, written from the claims on the first run |
| facet_ideas | 组合式想法 | research | none | facet_ideas (`tasks/facets/`) | the paper and its references split into purpose, mechanism and evaluation (quotes checked), recombined, the best `count` written up with a novelty check |
| deep_read | 论文精读 | research | none | deep_read (`tasks/deepread/`) | `work` read at five levels (sentence to verbatim, every statement quoted), derivations explained step by step from the work's own equations, references marked cited, read or new; read.html |
| source_independence | 支持文献独立性 | research | none | source_independence (`tasks/independence/`) | every claim's supporting works form two or more independent groups: works sharing an OpenAlex author (and, with `by_dataset`, a dataset) count once, self-citation chains marked |
| claim_support | 主张与文献对照 | research | claim_support | none | every claim is set against two or more works of the .bib, each with a quote; a contradicting work says how the paper answers it |
| literature_table | 文献对比表 | research | literature_table | none | three or more compared works of the .bib, every cell filled or "not reported" |
| taxonomy | 分类体系 | research (survey) | taxonomy | none | every surveyed work of literature.md sits in a category of taxonomy.md and every category holds a work |
| research_map | 研究地图 | research | none | research_map (`tasks/worldmap/`) | every claim has evidence or a planned experiment; map.html draws the argument map (claims, evidence, experiments, findings, sections) and the world model kept in `workflow/world.json` (entities, relations with checked evidence, open questions, next tasks), with the findings of `other_projects` and the author's other OpenProgram projects |
| campaign | 无人值守研究 | research | campaign | campaign (`tasks/campaign/`) | workflow/campaign.md (tasks per step, the agent's work, cron, budget, end date) is approved; the author starts it on the page, OpenProgram's scheduler runs a step on every tick, and it stops itself at the budget or the date, logging each step in campaign_log.md |
| lab_meeting | 组会讨论 | research | none | lab_meeting (`tasks/labmeeting/`) | specialists and a scientific critic discuss `agenda` for `rounds` rounds over the project's files, every fact cited and checked; decisions, disagreements and action items mapped to tasks or proposed experiment_plan rows; kept in workflow/meetings/ |
| thematic_analysis | 质性主题分析 | research | none | thematic_analysis (`tasks/qualitative/`) | transcripts in `data` coded by two blind coders (quotes checked), a codebook (workflow/codebook.md, author-owned), Cohen's kappa per code ≥ 0.6, themes for `question` with prevalence and quotes, a saturation curve |
| human_eval_build | 人工评测问卷 | research | none | human_eval_build (`tasks/humaneval/`) | a self-contained blind survey of `outputs` (order and side randomized, attention checks, repeated items), a guide workflow/eval_guide.md the author approves, a model pilot rater, the raters needed and a Prolific study text |
| human_eval_analyze | 人工评测分析 | research | none | human_eval_analyze (`tasks/humaneval/`) | `responses` with careless raters dropped: Krippendorff's alpha ≥ 0.667, Fleiss' kappa, Bradley–Terry win rates with bootstrap CIs per claim in workflow/human_eval.csv; evidence rows proposed in edits.json |
| meta_analysis | 荟萃分析 | research | none | meta_analysis (`tasks/metaanalysis/`) | `works` screened by independent screeners (kappa), effects extracted with checked quotes, risk of bias per work, DerSimonian–Laird pooling, I², outliers and duplicates flagged, forest.html and a GRADE certainty for `question` |
| consensus | 共识分析 | research (method, analysis, benchmark) | none | consensus_analysis (`tasks/consensus/`) | `runs` independent analyses of `question` over the results and `data` agree (two thirds or more), every number citing an existing row |
| funding_calls | 基金项目检索 | research | none | funding_calls (`tasks/funding/`) | open calls from Grants.gov and EU Funding & Tenders that fit the project (filtered by `funder`, at most `count`), each with a quoted reason and similar funded projects from NSF Awards and NIH RePORTER; closed calls dropped |
| call_requirements | 申请要求清单 | research | none | call_requirements (`tasks/funding/`) | workflow/call_requirements.json lists the requirements of `call` (URL or file), each with a quote found in the call; the grant draft is checked for page and word limits and required sections |
| grant | 基金申请书 | research | none | grant_proposal (`tasks/grant/`) | a proposal in the form of `funder` (NSF, NIH R01, ERC, NSFC 面上/青年, generic): aims mapped to claims and evidence, every number and citation found in the project, each section within its limit, with timeline and budget skeleton |
| experiment_plan | 实验计划 | research (method, analysis, benchmark) | experiment_plan | none | every claim has an experiment, a method paper an ablation per claim, and every done one names its evidence row |
| experiment_loop | 实验循环 | research (method, analysis, benchmark) | experiment | none | experiment.md (files to edit, command, metric, direction, budget, stop rule) is approved; every run is a row of results.tsv and each kept run is a commit that improves on the last kept one of its branch (optional `branch` and `parent` columns run directions in parallel; the best kept run is merged with a `merge` row) |
| topic_trends | 选题趋势 | research | none | topic_trends (`tasks/scouting/`) | yearly OpenAlex counts per query since `since`, normalized by all works, labelled rising, crowded, quiet or steady, with co-occurring topics |
| personal_rank | 新工作排序 | research | none | personal_rank (`tasks/scouting/`) | unhandled watch.csv rows and today's arXiv listing ranked by a logistic regression on SPECTER2 embeddings of the author's past decisions |
| data_explore | 数据探索 | research (method, analysis, benchmark) | none | data_explore (`tasks/discovery/`) | `budget` hypotheses about `data` tested by sandboxed scripts in a UCB tree, ranked by the surprise between the prior and posterior belief, in workflow/hypotheses.csv |
| method_lint | 实验方法检查 | research (method, analysis, benchmark) | none | method_lint (`tasks/methods/`) | the experiment code has no leakage, test-set tuning, evaluation on training data or unseeded randomness left open, in workflow/method_lint.csv |
| ablation_target | 消融定位 | research (method, analysis, benchmark) | none | ablation_target (`tasks/discovery/`) | each of `components` ablated on its own branch and run, ranked by its effect on the metric |
| hparam_sweep | 超参数搜索 | research (method, analysis, benchmark) | none | hparam_sweep (`tasks/sweep/`) | `trials` runs of experiment.md's Command over `space` within `seconds` (Optuna TPE, else Latin hypercube and local search), each committed and recorded in results.tsv on a sweep/<date> branch; best setting and parameter importance |
| power_analysis | 种子数功效分析 | research (method, analysis, benchmark) | none | power_analysis (`tasks/power/`) | every claimed comparison has enough seeds to detect `effect` (default: the claimed difference) at `alpha` with `power`, estimated from the pilot runs; a Seeds line for experiment.md proposed in edits.json |
| claim_stats | 显著性检验 | research (method, analysis, benchmark) | none | claim_stats (`tasks/methods/`) | each claim's comparison holds across seeds (paired or Welch t, bootstrap CI) at `alpha`; `stats_mode` popper combines falsification experiments by e-values |
| contamination | 基准污染检查 | research (benchmark) | none | contamination_check (`tasks/integrity/`) | the share of `sample` test items of `data` whose `ngram`-token windows occur in the `infini_index` corpora, with a paragraph for the datasheet |
| clean_rerun | 干净环境复现 | research (method, analysis, benchmark) | none | clean_rerun (`tasks/reproduce/`) | the kept runs `rerun` names (best, all or commits) give their recorded metric within `tolerance` in a fresh clone and the environment the repository declares |
| remote_run | 远程运行 | research (method, analysis, benchmark) | none | remote_run (`tasks/reproduce/`) | experiment.md's command ran at HEAD on `host` over ssh or Slurm (`scheduler`) and returned its metric and log; the import_tracking operation brings in W&B and MLflow runs |
| baseline_code | 基线代码 | research (method, benchmark) | none | baseline_code (`tasks/baselinecode/`) | every baseline has a repository found through Hugging Face Papers, Semantic Scholar, arXiv and GitHub, marked official when the paper links it or an author owns it, with license, last push, stars and archived flag; repo column proposed in edits.json |
| baselines | 基线复现 | research (method, benchmark) | baselines | none | every baseline ran in its reported setting with a log, and its status (reproduced, gap, failed) agrees with the tolerance |
| findings | 研究笔记 | research (method, analysis, benchmark) | findings | none | each tested hypothesis has its run, result and a verdict (supported, refuted, inconclusive) |
| dataset_release | 数据集发布 | release (benchmark) | datasheet | none | datasheet.md has a license, an existing location and every datasheet part |
| rebuttal | 审稿回复 | release | rebuttal | none | the reviews are pasted, every concern of concerns.csv is typed and answered (an experiment concern cites evidence or a promise), each reviewer's part fits the limit, and the author approved response.md |
| text_overlap | 文本重合检查 | release | none | text_overlap (`tasks/overlap/`) | no uncited passage of 25 words or more overlaps a cited work or a search result, and text reused from `own_papers` is cited or noted in workflow/text_overlap.md |
| media | 播客与讲解视频 | release | none | paper_media (`tasks/media/`) | a two-host podcast (`media_kind` podcast) or a narrated video of the slides or pages (video) of about `minutes` minutes, every number in the script found in the paper; macOS `say` and ffmpeg make the audio and video |
| submission_check | 投稿前检查 | release | none | submission_check (`tasks/submission/`) | the compiled paper has no LaTeX error or undefined reference, keeps the page limit of venue.md, reveals no author in a double-blind submission, and embeds its fonts |
| arxiv_package | arXiv 打包 | release | none | arxiv_package (`tasks/submission/`) | arxiv.zip holds every file the paper uses and its .bbl, with comments stripped, in `drafts/arxiv_package/` |
| paper_statements | 论文声明 | release | none | paper_statements (`tasks/statements/`) | statements.tex drafts the availability, CRediT, competing interests, funding and ethics or impact statements (`kinds`, default the venue's plus availability) from the project's facts only; unknown facts left as [TODO] |
| ai_disclosure | AI 使用声明 | release | none | ai_disclosure (`tasks/submission/`) | a disclosure of what the project's runs and AI-co-authored commits did is drafted in `drafts/ai_disclosure/` for the author to correct |
| venue_pick | 选投会议 | release | none | venue_pick (`tasks/scouting/`) | 3–5 venues from where the references and similar works were published, with the next deadline, page limit and anonymity |
| hidden_text | 隐藏文字检查 | release | none | hidden_text_check (`tasks/integrity/`) | no invisible text in the PDF or source (white, tiny, off-page, render mode 3) and no prompt aimed at AI reviewers |
| image_reuse | 图片复用检查 | release | none | image_reuse (`tasks/integrity/`) | no figure reused from `own_papers` without credit and no duplicated figure, by perceptual hashes |
| talk_prep | 报告问答准备 | release | none | talk_prep (`tasks/present/`) | 15 audience questions with answers grounded in the paper or evidence, slides checked against `minutes`, optional `audio` |
| paper_agent | 论文工具包 | release (method, benchmark) | none | paper_agent (`tasks/present/`) | the method of `code_dir` wrapped as an MCP server whose generated tests reproduce the paper's kept results |
| after_release | 发表后跟踪 | release | none | after_release (`tasks/scouting/`) | citing works of `work` with their stance (Semantic Scholar contexts), alphaXiv and OpenReview comments, new since the last run, in workflow/after_release.csv |
| impact_forecast | 引用预测 | release | none | impact_forecast (`tasks/impact/`) | citations at 1, 2 and 3 years as quantiles of comparable OpenAlex works 3–6 years old, weighted by similarity; correlated factors labelled as such; actual citations compared once published |
| tech_transfer | 技术转化评估 | release | none | tech_transfer (`tasks/impact/`) | technology readiness level (NASA/EU TRL 1–9) with checked evidence, and close patents from the USPTO Open Data Portal (USPTO_API_KEY) or PatentsView with their overlap per claim |
| writing_provenance | 写作溯源 | release | none | writing_provenance (`tasks/provenance/`) | each word of the paper attributed from the Git history to the author, AI-generated, AI-edited (applied edits.json), pasted (matching a compared source) or bulk; provenance.html with a replay per paragraph and a disclosure sentence |
| camera_ready | 终稿检查 | release | none | camera_ready_check (`tasks/venue/`) | the final version is de-anonymized, has acknowledgments and the venue's rights and CCS blocks, no draft marks, and keeps the camera-ready page limit |
| anonymous_package | 匿名补充材料 | release | none | anonymous_package (`tasks/venue/`) | supplementary.zip of `include` has no names, affiliations, emails, personal paths, Git history or notebook outputs (`anonymize` list or replace) |
| venue_move | 换投会议 | release | none | venue_move (`tasks/venue/`) | a plan to move to `target_venue`: template, bib style, sections, page budget and anonymity; `write_venue` yes writes the new venue.md |
| submission_metadata | 投稿表单信息 | release | none | submission_metadata (`tasks/venue/`) | TL;DR, keywords, primary area from the venue's list and a lay summary, numbers found in the paper |
| croissant | Croissant 元数据 | release (benchmark) | none | croissant_check (`tasks/outreach/`) | workflow/croissant.json is valid Croissant 1.0 with the RAI fields; the croissant_draft operation drafts it |
| publication_list | 论文列表 | release | none | publication_list (`tasks/publist/`) | the `author`'s works from OpenAlex, ORCID and DBLP, each once (preprint and published merged) with arXiv, DOI, PDF, code and page links; likely namesake works held for confirmation; publications.html and .bib |
| project_page | 项目主页 | release | none | project_page (`tasks/outreach/`) | a project page and a social thread with the paper's numbers only and working links; `publish` yes copies it to docs/ |
| venue_checklist | 会议清单 | release | checklist | none | every item of the checklist that `version` names is ticked; it lists only what no task checks |

A gate that reads another task's artifact uses only its ids. It does not judge that artifact: unapproved claims block the idea task, not the plan.

## Kind of work and progress

`workflow/project.md` holds the paper's kind of work (`method`, `analysis`, `survey`, `benchmark`, `position`) and its main claim (`brief.py`). A task's `kinds` lists the kinds it serves; the app lists only those, and a survey or position paper's plan cards may cite BibTeX keys as evidence. `progress.py` reads the folder and sorts the tasks of the kind into done, doing and todo, each with what it saw and what is missing, in `workflow/progress.json`; checks outrank it (a task whose check fails is never done), and the author corrects and confirms it in the app. Each task also carries `goal` (when it passes, in Chinese, for the app), `brief` (how its page's agent works) and `approves` (the file whose `Approved:` line only the author sets, from the app).

## Adding a task

1. When the task needs a new check, add a `Gate` to `registry.GATES`. It holds a function `(paper, created, inputs) -> problems` and the instruction to show when it fails. `needs` names the inputs it reads (it is skipped while they are empty); `live=True` marks a gate that calls the network, which the status view skips.
2. Add a `Task` row to `registry.TASKS` with its gates, artifacts, inputs and required inputs. When a workflow does the work, give it one `Worker`, whose `run(paper, inputs)` builds every argument of that workflow in code. Something that does two things is two rows.
3. Add the artifact templates to `templates/` and a test. `check_registry()` must stay empty.

Plans and claims are shown to the author in Chinese. A plan states only what the figures, tables and data contain.

## Layout

| Path | Contents |
| --- | --- |
| `workflow.py` | The entry runs the task's own gates and then its workflow. It also saves the last result and builds the status view. |
| `registry.py` | Gates, workers and the task table. |
| `stages.py` | The gate checks on the artifacts, and the saved state, in plain code. |
| `release.py` | The code release and deliverables checks. |
| `trail.py` | The run record every task and workflow here writes (`run.json` and `run.md`); see below. |
| `tasks/survey/` | The research_survey workflow: a field surveyed into one offline HTML page. See its README. |
| `tasks/revision/` | The paper_revision_check workflow: a draft checked against a committed baseline, with the red/green PDF. See its README. |
| `tasks/ideas/` | The idea_generation workflow: ideas from the surveyed gaps, checked against the literature and ranked. See its README. |
| `tasks/review/` | The paper_review workflow: the paper reviewed as its venue would, round after round. See its README. |
| `tasks/figures/` | The paper_figures workflow: the plotting scripts checked against the figures4papers skill and run. See its README. |
| `tasks/citations/` | The citation_check workflow: references checked against free sources. See its README. |
| `templates/` | The starting artifacts. |
| `app/` | The Auto Research application: each task as a page with its files and its own conversation, where the author's agent does the work in the paper's folder. See `app/README.md`. |

## Run record

`trail.py` records each run: the roles it used, every stage's context and output, and the files it produced. It makes no model call and does not change what the model sees.

| Item | Contract |
| --- | --- |
| Input | `Trail(workflow, args, roles)`, where `roles` maps a name to an `Agent`. Code stages call `trail.step(name, **facts)`. A model stage wraps its role call in `with trail.call(name, role, function, ctx, schema) as rec:` and sets `rec.output`. |
| Output | `trail.save(out, **files)` writes `run.json` (schema 1) and `run.md` into the run's output folder. |
| Live progress | `with trail.listen(callback):` receives every `step` and every model call's start and end while the block runs. |
| Failure | An exception inside `trail.call` is recorded as `error` and raised again. |
| Tests | `tests/test_trail.py` |

| run.json key | Contents |
| --- | --- |
| `schema` | `1`. Raise it on any breaking change; readers check it. |
| `workflow`, `started`, `finished` | The entry name and ISO times. |
| `args` | The entry's arguments. |
| `roles` | For each role: `instructions`, `effort`, `model` ("caller's model" when not set) and `tools`. |
| `stages` | In order. A code stage holds `stage`, `kind: "code"`, `at` and its facts. A model stage holds `stage`, `kind: "model"`, `role`, `function`, `instructions` (the function docstring the framework sends), `context` (every Context block as resolved), `schema`, `output`, `seconds` and `error` when it failed. |
| `outputs` | The files the run produced, by name. |

Text longer than 200,000 characters is cut and marked.
