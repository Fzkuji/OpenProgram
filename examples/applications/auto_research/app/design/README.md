# Auto Research design

The usage flow and screens agreed with the author. The first canvas
(https://claude.ai/artifact/PhRELR6wmcddj8K5F9PMQi) fixed the welcome page, the
workspace and the task list; its artboards are kept here. The task page was later
redrawn around a conversation (layout "A"): the task's files above, the last
exchange under them, OpenProgram's composer at the bottom, no right sidebar.

| File | What it shows |
| --- | --- |
| `Main.dc.html` | The flow: open the app, enter a project, pick a task. It also lists what is always present. |
| `Welcome.dc.html` | No project open: open a folder (system chooser, ⌘O) or pick from OpenProgram, and the recent projects. A folder that is not a repository gets a notice in place. |
| `Workspace.dc.html` | An open project: the project switcher, tasks by phase with their state and the status bar. |
| `Task.dc.html` | The first task page; superseded by layout A (see `../README.md`). |
| `canvas.json` | The layout of the canvas. |

The `.dc.html` files are Design canvas artboards. They render on the canvas, not
when opened directly in a browser. Screens are mock-ups: bracketed text such as
`[分支]` stands for live data.

What an author needs from a task page, which the page keeps:
- See what the task has made: the figure, the PDF page, the claims, the table, without
  leaving the app.
- Know what is still missing: the last check's findings, on top, short.
- Say what to change in words, to an agent that already knows the task and works in
  the paper's folder; see what it changed as soon as it ends.
- Come back later to the same conversation: one per task.
- Decide: the agent never approves anything; the author runs the check and approves.
- A blocked task says why and links to the task that unblocks it.
- The page is built from OpenProgram's own component kit (see `../README.md`).

## What the app learned from other research agents

`coverage.md` lists every surveyed product feature by feature, with the task that does the same.

The app's tasks were set against the automated-research systems of 2024–2026. A feature
became a task when an author writing one paper needs it, and it is shaped so the check
stays plain code and the agent does the work in the task's conversation.

| Feature | Taken from | In the app |
| --- | --- | --- |
| Keep-if-better experiment loop | Karpathy's autoresearch (one editable file, fixed time budget, one metric, results.tsv); Agent Laboratory's mle-solver | 实验循环: experiment.md spec the author approves, results.tsv, kept runs must be commits that improve |
| Staged experiments and ablations | AI Scientist v2 (preliminary → tuning → agenda → ablations) | 实验计划: each claim's main, ablation, robustness and significance experiments, done ones pointing at evidence |
| Baseline reproduction | AI-Researcher, DeepScientist | 基线复现: reported vs reproduced within a tolerance, with logs |
| Findings memory | DeepScientist's Findings Memory, Kosmos's world model, AgentRxiv | 研究笔记: append-only findings.md, read before proposing ideas |
| Every number traced to code | data-to-paper, Kosmos | 实验证据 (evidence.csv), kept |
| Concurrent-work watch | Undermind and Elicit alerts | 新工作跟踪: queries from the claims, new papers judged overlap / related / unrelated, overlaps stay open until handled |
| Claim–literature contradiction | FutureHouse ContraCrow, Consensus | 主张与文献对照: each claim against two or more works with quotes |
| Extraction table | Elicit, SciSpace | 文献对比表: author's columns, every cell from the work |
| Survey taxonomy | AutoSurvey, STORM | 分类体系 (survey kind) |
| Review → revise rounds | CycleResearcher / CycleReviewer, AI Scientist reviewer | 论文评审: each round says which weaknesses are resolved; stop when the score stops rising |
| Rebuttal | none automate it (Zochi's was written by people) | 审稿回复: concerns split and typed, answered from evidence, within the limit, approved by the author |
| Dataset release | benchmark papers' datasheets (Gebru et al.) | 数据集发布 (benchmark kind) |
| Plan first, then act | Gemini Deep Research's editable plan, Agent Laboratory co-pilot, Claude Code plan mode | every task page's agent lists its changes and ends with 「等你确认：就这样改」; the page answers with one button |
| Pairwise idea tournament | AI co-scientist (Elo over debates), Sakana AI Scientist's idea archive | 想法生成: every pair of the 1–8 ideas refereed once, ranked by Elo; known ideas last |
| Coverage estimate | Undermind's estimate of how much of the relevant literature was found | 领域综述: capture–recapture over the overlap of arXiv, Semantic Scholar and OpenAlex; below 60% the run asks for more queries |
| Cost per paper | AI Scientist's and Agent Laboratory's reported cost per paper | every run's trail adds the host's metered tokens and list price; the overview shows the project's total |
| Preregistration | data-to-paper (hypothesis and analysis plan fixed before the data) | 实验计划: 锁定计划 writes workflow/preregistration.json; a locked row changed later fails the check until the author locks again, and every lock stays in the history |
| PRISMA flow | Elicit's systematic review (PRISMA 2020) | 领域综述: the report counts returned, unique, screened, excluded and included works; each decision and reason is in candidates.json |
| Proofreading: math, notation, numbers, statistics, missing citations, language | AAAI-26 AI-review pilot, paperreview.ai, Overleaf Citation reviewer, Writefull, Paperpal, OpenAI Prism | 论文校对: one checker call per section, quotes verified in code, candidate works for uncited claims, language edits applied one at a time; three titles and two abstracts |
| Retractions and missing key works | Scite Reference Check, Connected Papers, Litmaps, ResearchRabbit | 引文图谱: OpenAlex and Crossref retraction and correction notices, works your references cite but you do not, graph.html |
| Library import | Zotero sync in ResearchRabbit, Elicit, SciSpace | the import_library operation merges a .bib or CSL-JSON export without removing anything |
| Reviewer panel and area chair | AgentReview, AAAI-26 pilot, CycleReviewer / OpenReviewer score prediction | 论文评审: 1–5 reviewers with different focuses, an area chair's meta-review, a rough acceptance estimate for ICLR/NeurIPS/ICML, ACL ARR and CVPR-family venues |
| Pre-submission check | Overleaf Error Assist, Paperpal's submission check, venue format checkers | 投稿前检查: LaTeX errors at their line, undefined references, page limit, anonymity, embedded fonts |
| arXiv bundle | Google's arxiv_latex_cleaner | arXiv 打包: used files only, .bbl required, comments stripped, manifest |
| AI-use disclosure | Agents4Science's AI-involvement checklist, venue AI policies | AI 使用声明: drafted from the runs and AI-co-authored commits |
| World model and argument map | Kosmos's world model, Co-STORM's mind map | 研究地图: argument map in plain code; entities, relations with checked evidence, open questions and next tasks kept in workflow/world.json across runs |
| Shared findings across projects | AgentRxiv | 研究地图 reads the findings.md of the author's other projects |
| Consensus of independent analyses | FutureHouse Finch, Kosmos | 共识分析: 2–7 runs from different angles, numbers checked against the cited rows, only the agreement reported |
| Parallel experiment branches | AI Scientist v2's agentic tree search, AlphaEvolve | 实验循环: branch and parent columns, each branch improves on its own best, the best kept run is merged |
| Support/contradict meter and study type | Consensus | 主张与文献对照: a study column, claims supported only by position or survey works are flagged, counts per claim on the page |
| Similarity check | iThenticate, Paperpal, Turnitin (self-plagiarism) | 文本重合检查: 8-gram overlap with the author's earlier papers, the cited arXiv works' full text and search results |
| Audio and video overviews | NotebookLM, Paper2Video | 播客与讲解视频: grounded script, macOS voices, ffmpeg |
| Evolving idea population | AI co-scientist (generation, reflection, Elo tournament, evolution, proximity, meta-review) | 想法生成 with rounds: an evolver combines, refines and simplifies the leaders, Swiss-paired matches, near-duplicates merged, a meta-review steers the next round, up to 40 ideas alive |
| Reviewer grounded in real reviews | CycleReviewer and OpenReviewer (trained on OpenReview) | 论文评审: real reviews of similar ICLR/NeurIPS/ICML submissions from the OpenReview API as calibration examples, and the acceptance estimate fitted on their ratings and decisions |
| Unattended campaigns | Kosmos / Edison, DeepScientist, Agent Laboratory's autonomous mode | 无人值守研究: an approved campaign.md, a recurring scheduler task, a dollar budget and an end date, commits only on campaign/<date> branches |
| Commercial similarity databases | iThenticate, Turnitin, Originality.ai, Winston AI | 文本重合检查 sends the prose to Winston AI or Originality.ai when the author set a key and opted in |
| Clean-environment reproduction | Code Ocean, PaperBench | 干净环境复现: a fresh clone and environment rerun the kept runs within a tolerance |
| Paper settings against code | Paper2Code, PaperBench's rubric | 实验设置核对: settings extracted from the paper, compared with argparse defaults and configs |
| Experiment tracking and remote compute | W&B, MLflow, Bohrium | the import_tracking operation; 远程运行 over ssh or Slurm |
| Camera-ready and venue forms | ACM TAPS, IEEE PDF eXpress, OpenReview forms | 终稿检查; 投稿表单信息 |
| Anonymous code | Anonymous GitHub | 匿名补充材料 |
| Reformatting for another venue | SciSpace / Typeset formatter | 换投会议 writes the plan; the page agent applies it |
| Baseline numbers from the sources | Papers with Code, Elicit extraction | 基线数字核对 with quotes checked in the fetched full text |
| Recommendations per passage | Keenious, Semantic Scholar recommendations | 逐段文献推荐 |
| Reading notes | Zotero and ReadCube annotations | the import_annotations operation |
| Skimming reader | Semantic Reader / Scim | 速读测试 |
| Dataset metadata | MLCommons Croissant, Hugging Face dataset cards | Croissant 元数据, with the croissant_draft operation |
| Project page and thread | Paper2Web, AutoPage | 项目主页 |
| Formal proofs | Lean / mathlib provers | 定理形式化 with the lean_draft operation |
| Grant proposals | Grantable, Instrumentl, Granted AI, NIH/NSF aims helpers, 国自然申请书助手 | 基金申请书: aims from the claims, grounded numbers and citations, funder section limits |
| Method errors in experiment code | scicode-lint, LeakageDetector, MLE-STAR's checker | 实验方法检查: AST rules confirmed by a reviewer with quotes |
| Statistics and falsification | POPPER | 显著性检验: t-tests and bootstrap across seeds; popper mode with e-values |
| Hidden prompts in PDFs | PhantomLint, hiddenprompt-scan | 隐藏文字检查 |
| Benchmark contamination | infini-gram | 基准污染检查 |
| Figure reuse | Proofig, ImageTwin | 图片复用检查 by perceptual hashes |
| Venue recommendation | JournalFinder, Springer Journal Suggester | 选投会议 with ccfddl deadlines |
| Topic trends | 知网 AI 研学 | 选题趋势 |
| Personal recommender | Scholar Inbox | 新工作排序 |
| After publication | Scite alerts, alphaXiv, OpenReview | 发表后跟踪 |
| Discovery from data | Ai2 AutoDS / AutoDiscovery | 数据探索 with Bayesian surprise |
| Ablation to the component that matters | MLE-STAR | 消融定位 |
| Team notes into the knowledge graph | Microsoft Discovery Bookshelf | the notes_ingest operation, reviewed in 研究地图 |
| Paper as an agent tool | Paper2Agent | 论文工具包 |
| Talk rehearsal | rehearsal and Q&A tools | 报告问答准备 |
| Facet recombination | Scideator | 组合式想法: purpose, mechanism and evaluation facets recombined, novelty checked |
| Zoomable reading, derivations, reading history | PaperFold, Nujan, CiteSee | 论文精读: five levels, step-by-step derivations, references marked by the author's citing and reading |
| Virtual lab meeting | Stanford Virtual Lab | 组会讨论: the author as PI, specialists and a critic, cited facts, action items |
| Bayesian-optimisation sweeps | Ax / BoTorch, Optuna | 超参数搜索: TPE trials each recorded in results.tsv |
| The author's own style | PaperSpine, Writing in the Sciences (SciWrite) | 写作风格: workflow/style.md and exact edits |
| Whole-paper translation | gpt_academic | 论文翻译: protected LaTeX, glossary, xelatex |
| Method diagrams and captions | PaperBanana, AutomaTikZ / TikZero, Scicapenter, SciencePlots | 方法示意图 and 图表标题检查 |
| Impact forecast | HLM-Cite, newborn-article impact prediction | 引用预测 from comparable works' trajectories |
| Technology transfer | Academic Commercialization Agent | 技术转化评估: TRL and patent landscape |
| Writing provenance | Grammarly Authorship | 写作溯源: every word attributed from Git history and the runs, replay per paragraph |
| Human readers before submission | ResearchHub (paid named reviewers), friendly review | 投稿前找人审读: conflict-checked candidates and drafts the author sends |
| Subscribed and open-access full text | Wiley AI Gateway, Unpaywall | text overlap reads the author's PDFs (workflow/papers/, Zotero) and open-access copies |
| Qualitative coding | thematicanalysis.ai, NVivo / ATLAS.ti / MAXQDA AI coding | 质性主题分析: two blind coders, kappa, themes with quotes |
| Automated proving | Seed-Prover, DeepSeek-Prover, Harmonic Aristotle | 定理证明: model proposes, Lean checks, errors drive the next round |
| Power analysis before the runs | Curasynth | 种子数功效分析: noncentral-t power from the pilot runs' paired variance |
| Funding call search and requirements | Proposia | 基金项目检索 and 申请要求清单: Grants.gov, EU Funding & Tenders, quoted requirements checked against the draft |
| Independence of supporting sources | Hyperresearch | 支持文献独立性: OpenAlex authorships group works by the same people |
| Baseline code | DeepGit, Papers with Code | 基线代码: official repository by paper link or author ownership |
| Back-matter statements | Curasynth, CRediT, journal data policies | 论文声明: drafted from facts, every URL, number and name checked |
| Human evaluation studies | Prolific, Chatbot Arena | 人工评测问卷 and 人工评测分析: blind survey, attention checks, alpha, Bradley–Terry |
| Pooling and certainty of evidence | GRADEpro, DistillerSR, Rayyan | 荟萃分析: dual screening, quoted extraction, random effects, GRADE |
| Code documentation tied to the paper | DeepWiki | 代码 Wiki: AST index, call graph, paper-to-function links checked in code |
| Copied code and licenses | Codequiry, Software Heritage, ScanCode | 代码来源与许可证核查: SWHIDs checked against the archive, winnowing for adapted files, SPDX compatibility |
| Software citation and archive DOI | figshare, Zenodo GitHub integration, CFF | 软件引用文件: CITATION.cff, .zenodo.json, codemeta.json, DOI found and resolved |
| Publication list | BibBase, Google Scholar profiles | 论文列表: OpenAlex, ORCID, DBLP merged, namesakes held for the author |
| The paper's own numbers against the runs | PaperDelta, Agent-Cockpit, celsus | 结果数字核对: stale, misattributed and derived-claim checks with Git history |
| Missing strongest baselines | LINEAGE, reviewers' first question | 漏掉的更强基线: citing works of the dataset and baselines, quoted numbers, direction-aware comparison |
| Human approval at checkpoints | Agent Laboratory co-pilot mode, AI co-scientist | claims, plan cards, the experiment spec and the rebuttal are approved only by the author's button |

Also covered by earlier rows: novelty checks (FutureHouse Owl, AI co-scientist), editable research plans
(Gemini, OpenAI Deep Research), cited reports (Perplexity, Claude Research), chat with the
project's papers (NotebookLM, PaperQA2, SciSpace: the task conversations read the files),
inline drafting (Jenni, Prism: the plan task's agent writes sections along the approved
card), poster and slides (Paper2Poster: 海报与报告材料), and paper-to-code (PaperCoder:
基线复现 and 代码发布).

Where an original needs what one author's machine cannot have, the app takes the closest
working form: reviewer models trained on OpenReview become a prompted panel calibrated on
real OpenReview reviews; iThenticate and Turnitin, sold to institutions only, are reached
through the Turnitin Core API with the institution's key (TCA_BASE_URL and TCA_API_KEY),
and without one through the commercial APIs an individual can buy plus the open-source
overlap check. Every step that
changes the paper's claims stays under the author's approval.

## Results: what the author does with a run

`results-flow.html` compares three ways to handle what a run made; option A was chosen.
A run's report, proposed edits (`edits.json`), questions (`questions.json`) and made
files become one result card on the task page, and nothing is copied by hand:

- Proposed edits show the problem and the − / + lines, with 应用 / 忽略 and 全部应用.
- Questions only the author can answer show their options as buttons (收录 / 排除); the
  task's handler applies the answer and later runs keep it.
- A made file with a place in the project (`results.PLACES`) is put there with 放入.
- A failed check offers 修复, which hands the problems to the task's agent.
- The overview lists 待处理 (runs still asking something) and 改动 (uncommitted files,
  each with the task that changed it). The 改动 page shows each file's diff, 撤销 per
  file and 提交 with a message written from those tasks.

Taken from: Codex's turn summary with Undo and its changes pane (revert per file, commit
with a generated message); Claude's "Copy to project" for made files; GitHub's
"Viewed" mark for results the author has dealt with (归档). Buttons carry one verb, no
sentences.
