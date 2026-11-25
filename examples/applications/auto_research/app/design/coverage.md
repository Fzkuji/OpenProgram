# Feature coverage against other research agents

Every product surveyed in 2026-11, each of its distinctive features, and where Auto Research
does the same. A task is named by its title in the app (its registry name in brackets).

| Product | Feature | In Auto Research |
| --- | --- | --- |
| Sakana AI Scientist v1 | Idea → code → figures → LaTeX → self-review pipeline | The task list from 想法生成 (idea_generation) through 实验循环 (experiment_loop), 论文图表 (figures) and 论文评审 (paper_review) |
| Sakana AI Scientist v2 | Agentic tree search over experiments, parallel branches | 实验循环: `branch` and `parent` columns, per-branch improvement, merge of the best kept run |
| Sakana AI Scientist v2 | Vision model checks figures | 论文图表 redraws every figure from its script and checks it against the figure rules; the page agent looks at each redrawn figure |
| Sakana AI Scientist v2 | Automated reviewer | 论文评审 with a reviewer panel and area chair |
| Agent Laboratory | Literature, experiment and writing solvers | 调研材料 / 领域综述, 实验循环, 章节计划 (plan) and 论文检查 (paper_check) |
| Agent Laboratory | Co-pilot mode: human at each stage | Every task page: the agent proposes, the author confirms (就这样改); claims, plan cards, experiment spec, rebuttal and campaign need the author's approval |
| Agent Laboratory | Autonomous mode | 无人值守研究 (campaign) |
| AgentRxiv | Runs build on a shared preprint store | 研究地图 (research_map) reads the findings of the author's other projects |
| AI-Researcher (HKU) | Start from reference papers or from an idea | 项目类型 and 盘点: a project starts from whatever exists; 想法生成 from surveyed gaps or literature.md |
| AI-Researcher (HKU) | Benchmark of research levels | 基准 kind of work: 基线复现, 数据集发布 (dataset_release) and 实验证据 for a benchmark paper |
| DeepScientist | Findings memory, hypothesis → verify → analyse loop | 研究笔记 (findings), 实验循环, 共识分析 (consensus) |
| DeepScientist | Month-long campaigns | 无人值守研究 with budget and end date |
| Kosmos / Edison | Structured world model | 研究地图: workflow/world.json (entities, relations with checked evidence, open questions, next tasks) |
| Kosmos / Edison | Every statement linked to code or literature | 实验证据 (evidence.csv), 共识分析 citations to rows, 主张与文献对照 quotes |
| Kosmos / Edison | Hundreds of rollouts per run | 共识分析 runs 2–7 independent analyses; 无人值守研究 repeats steps on a schedule |
| FutureHouse Crow | Short cited answers | Any task conversation reads the project's papers and files |
| FutureHouse Falcon | Deep literature review | 领域综述 (field_survey) |
| FutureHouse Owl | "Has anyone done X?" | 想法生成's novelty check; 新工作跟踪 (watch) |
| FutureHouse Finch | Parallel analysis trajectories with consensus | 共识分析 |
| FutureHouse Robin | Agents chained end to end | 无人值守研究 chains the tasks named in campaign.md |
| FutureHouse Phoenix | Chemistry tools | Domain tools the author connects to OpenProgram (MCP servers) are available to every task page's agent |
| Google AI co-scientist | Generation, reflection, Elo tournament, evolution, proximity, meta-review | 想法生成 with `rounds` and `population` |
| Google AI co-scientist | Scientist adds ideas mid-run | The author adds ideas in the task conversation or to literature.md; the next run includes them |
| OpenAI / Gemini / Perplexity Deep Research, Claude Research | Editable research plan, long browsing, cited report | 领域综述 plans its scope first; reports cite every paper; the page agent proposes before acting |
| Gemini Deep Research | Report as audio | 播客与讲解视频 (media) |
| OpenAI Prism | Find literature and write it into .bib | 论文校对 (proofread) candidates for uncited claims; 引文图谱 (citation_graph) import_library |
| OpenAI Prism | Proofreading, image-to-LaTeX | 论文校对; the page agent converts tables and figures on request |
| Elicit | PRISMA systematic review | 领域综述 PRISMA flow and candidates.json with each decision and reason |
| Elicit | Extraction tables | 文献对比表 (literature_table) |
| Elicit | Weekly routines | 新工作跟踪 on a campaign schedule |
| Undermind | Iterative deep search with a coverage estimate | 领域综述 snowballing and gap search; coverage estimate |
| SciSpace | Chat with PDFs, literature tables | Task conversations; 文献对比表 |
| SciSpace Agent | 150+ tools | The page agent uses OpenProgram's tools |
| Consensus | Consensus Meter (yes/no counts) | 主张与文献对照 counts per claim on the page |
| Consensus | Study snapshot (type, size, venue) | 主张与文献对照 `study` column; claims supported only by position or survey works are flagged |
| Scite | Smart Citations (supporting / contrasting) | 主张与文献对照 stance column |
| Scite | Reference Check (retractions) | 引文图谱 retraction and correction notices |
| ResearchRabbit / Connected Papers / Litmaps | Citation graph, missing works | 引文图谱 graph.html and missing key works |
| ResearchRabbit / Litmaps | Zotero sync, new-paper alerts | import_library; 新工作跟踪 |
| STORM / Co-STORM | Perspective-guided questions, mind map | 领域综述 research lines; 研究地图 argument map |
| AutoSurvey / SurveyX / SurveyForge | Parallel section writing, outline from human surveys | 领域综述 chapters per line, baseline headings from existing surveys |
| OpenScholar | Retrieval, self-feedback, revise | 领域综述 audit and rewrite |
| PaperQA2 | Full-text RAG with passage citations | 领域综述 reads full text with locators; task conversations |
| PaperQA2 ContraCrow | Contradictions in the literature | 主张与文献对照 contradicts rows |
| data-to-paper | Hypothesis and analysis plan locked before the data | 实验计划 preregistration |
| data-to-paper | Every step traceable to data | 实验证据, 共识分析 |
| CycleResearcher / CycleReviewer | Review rounds, score prediction from a model trained on OpenReview | 论文评审 rounds; score model trained locally on ICLR submissions; calibration on real reviews |
| OpenReviewer | Venue review template | 论文评审 reviews to the venue's form |
| paperreview.ai | Review grounded in arXiv | 论文评审 with OpenReview anchors; 论文校对 with arXiv candidates |
| AgentReview | Reviewer biases, area-chair discussion | 论文评审 personas and area chair |
| AAAI-26 AI review pilot | Error finding, no score | 论文校对 math, notation, number and statistics checks |
| Zochi / Locus | End-to-end paper with human copilot | The whole task list under the author's approval |
| MLR-Copilot / ResearchAgent | Ideas from papers, reviewer agents refine ideas | 想法生成 with judge and evolver |
| Karpathy autoresearch | One editable file, fixed budget, keep if better, results.tsv | 实验循环 |
| AlphaEvolve | Evolution by an automatic score, population | 实验循环 branches; 想法生成 population |
| Paper2Poster | Poster layout | 海报与报告材料 (deliverables) |
| Paper2Video | Narrated video | 播客与讲解视频 |
| Paper2Code / PaperCoder | Paper to repository | 基线复现 (baselines) and 代码发布 (code_release) |
| Agents4Science | AI-involvement checklist | AI 使用声明 (ai_disclosure) |
| Overleaf AI Assist / Error Assist | Fix LaTeX compile errors | 投稿前检查 (submission_check) lists errors at their line; the page agent fixes them |
| Overleaf Citation reviewer, Writefull | Missing citations, language | 论文校对 |
| Paperpal / Jenni / Yomu | Polishing, continuation, plagiarism, citation style | 论文校对 edits; 章节计划's agent drafts along the plan card; 文本重合检查 (text_overlap); 参考文献核查 (citation_check) |
| Paperpal | Pre-submission check | 投稿前检查, 会议清单 (venue_checklist) |
| iThenticate / Turnitin | Similarity against closed databases | 文本重合检查 via Turnitin Core API with institutional credentials |
| Originality.ai, Winston AI | Plagiarism API | 文本重合检查 providers |
| NotebookLM | Audio and video overviews, slides, mind maps | 播客与讲解视频; 海报与报告材料; 研究地图 |
| Ai2 Asta | Paper finder, ScholarQA, DataVoyager | 领域综述; task conversations; 共识分析 over data files |
| arxiv_latex_cleaner | Clean arXiv source | arXiv 打包 (arxiv_package) |
| Cost reporting (AI Scientist, Agent Laboratory) | Cost per paper | Spend on the overview, per run in run.md |
| Code Ocean | Reproducible capsules | 干净环境复现 (clean_rerun) |
| PaperBench | Replication graded against the paper | 干净环境复现; 实验设置核对 (settings_check) |
| Weights & Biases / MLflow | Experiment tracking | import_tracking operation into results.tsv |
| Bohrium / HPC portals | Remote and cluster runs | 远程运行 (remote_run) over ssh or Slurm |
| ACM TAPS / IEEE PDF eXpress | Camera-ready format check | 终稿检查 (camera_ready) |
| Anonymous GitHub | Anonymized code for review | 匿名补充材料 (anonymous_package) |
| SciSpace / Typeset formatter | Reformat for another journal | 换投会议 (venue_move) |
| OpenReview submission form | TL;DR, keywords, primary area | 投稿表单信息 (submission_metadata) |
| Papers with Code | Results tables of cited works | 基线数字核对 (baseline_numbers) |
| Keenious | Recommendations for the passage being written | 逐段文献推荐 (related_by_paragraph) |
| Zotero / ReadCube | Highlights and notes | import_annotations operation into workflow/annotations.csv |
| Semantic Reader / Scim | Skimming highlights | 速读测试 (skim_test) |
| MLCommons Croissant | Dataset metadata with RAI fields | Croissant 元数据 (croissant), croissant_draft operation |
| Paper2Web / AutoPage | Project page from the paper | 项目主页 (project_page) with a social thread |
| Lean / mathlib provers | Formal statements and proofs | 定理形式化 (lean_check), lean_draft operation |
| Grantable / Instrumentl / Granted AI | Grant proposals in the funder's form | 基金申请书 (grant) for NSF, NIH R01, ERC, NSFC |
| scicode-lint / LeakageDetector 2.0 | Methodology errors in ML code | 实验方法检查 (method_lint) |
| MLE-STAR | Checker agent; ablation to the block that matters | 实验方法检查; 消融定位 (ablation_target) |
| POPPER | Sequential falsification with e-values | 显著性检验 (claim_stats) popper mode |
| PhantomLint / hiddenprompt-scan | Hidden prompt injection in PDFs | 隐藏文字检查 (hidden_text) |
| infini-gram contamination checks | Test items in pretraining corpora | 基准污染检查 (contamination) |
| Proofig / ImageTwin | Image reuse and duplication | 图片复用检查 (image_reuse) |
| Elsevier JournalFinder / Springer Journal Suggester / WTS | Where to submit | 选投会议 (venue_pick) |
| 知网 AI 研学 | Topic trend analysis | 选题趋势 (topic_trends) |
| Scholar Inbox | Recommender trained on the author's ratings | 新工作排序 (personal_rank) |
| Scite alerts / alphaXiv / OpenReview comments | Who cites the paper, public comments | 发表后跟踪 (after_release) |
| Ai2 AutoDS / AutoDiscovery | Open-ended discovery from data by Bayesian surprise | 数据探索 (data_explore) |
| Microsoft Discovery Bookshelf | Team notes into a knowledge graph | notes_ingest operation, reviewed in 研究地图 |
| Paper2Agent | Paper and code as an MCP server with tests | 论文工具包 (paper_agent) |
| Talk rehearsal tools | Audience questions and answers | 报告问答准备 (talk_prep) |
| Denario / CMBAgent | Modular end-to-end pipeline, journal-style LaTeX | The task list; 换投会议 for the journal template |
| InternAgent-1.5 (NovelSeek) | Generation, verification, evolution; experiment memory; paper replication | 想法生成 with evolution; 实验循环 and 研究笔记; 干净环境复现 |
| Ai2 CodeScientist | Code-based experiments from paper and code blocks | 实验循环, 基线复现 |
| aiXiv | AI review and revision loop | 论文评审 rounds with 论文检查 |
| q.e.d / OpenAIReview / Reviewer3 | Author-side pre-review | 论文评审, 论文校对; AI-text detection through 文本重合检查 providers |
| Gemini for Science | Hypothesis generation, literature insights | 想法生成, 领域综述 |
| Kimi / 秘塔 deep research | Long cited reports | 领域综述 |
| Autoscience Carl / Intology Locus | End-to-end ML research agent | The task list with 无人值守研究 |
| ShinkaEvolve | Sample-efficient program evolution | 实验循环 branches with 消融定位 |
| Claude Science | Reviewer agent checks citations and math; every figure kept with the code that drew it | 论文校对 math and number checks, 参考文献核查; 论文图表 redraws every figure from its script |
| Digital Science Papers AI | Draft, data and references read together; edits shown beside the original to accept or reject | Task conversations read the project; 论文校对 edits.json applied one at a time |
| Claude Prism (open source) | Offline LaTeX + Python workspace | The project is a local Git repository; every task runs on it |
| ScholarCopilot, ILCiteR, CiteBART | Citation suggested for the sentence being written | 逐段文献推荐; 论文校对 candidates for uncited claims |
| XtraGPT, Quillbot, Wordvice, HyperWrite, Textero, Writesonic | Revise a passage on instruction, paraphrase, polish | The page agent edits a passage on request; 论文校对 language edits |
| MoDeST, HeadlineAnalyzer | Title generation and scoring | 论文校对 proposes three titles and two abstracts |
| Manubot | Manuscript built by CI from Git | The project's Git repository; 投稿前检查 compiles it |
| PaSa, Iris.ai, Enago Read, AMiner, Inciteful, Emergent Mind, Cool Papers, Explainpaper, ChatDOC, AskYourPDF, ScholarAI, MirrorThink, Uni-finder, GenGO, iFlytek 星火科研 | Agentic paper search, chat and summaries | 领域综述, 调研材料, task conversations |
| GPT Researcher, DeerFlow, Local Deep Research, open_deep_research, Manus, Grok DeepSearch, Copilot Researcher, Qwen Deep Research | Deep research reports | 领域综述 |
| ORKG, BGPT | Structured comparison of contributions, structured evidence | 文献对比表 |
| Covidence, PICO Portal, SixSentences | Systematic-review screening and deduplication | 领域综述 PRISMA flow, candidates.json with each decision |
| Valsci | Claim verification | 主张与文献对照 |
| llm-for-zotero, PapersGPT, Zotero-GPT | Chat over the Zotero library | import_library and import_annotations; task conversations |
| Lacuna MCP, paper-search-mcp, Lune Research | Literature as MCP tools | MCP servers connected to OpenProgram are available to every task page's agent |
| MinerU, Docling, Nougat, GROBID, Marker | PDF to structured text | pdftotext and arXiv LaTeX sources inside the reading tasks |
| SciMON, Nova, IDEAgent, VirSci, MOOSE, Principia | Novelty-optimised idea generation | 想法生成 with novelty check and evolution |
| Curie | Rigor and reproducibility checks on experiments | 实验方法检查, 干净环境复现 |
| celsus | Every number in the LaTeX checked against a replication | 结果数字核对 (result_numbers), 干净环境复现, 基线数字核对 |
| AIDE (Weco), RD-Agent (Microsoft), OpenEvolve, ASI-Arch, AlphaResearch, Arbor | Tree or evolutionary search over experiments | 实验循环 branches; 消融定位 |
| DeepAnalyze, PandasAI, Jupyter AI, Notebook Intelligence | Data analysis agents | 数据探索, 共识分析 |
| ARIS (Auto-Research-In-Sleep) | Overnight runs, one model reviewing another | 无人值守研究; 论文评审 with its own `reviewer_model` |
| XScientist | Versioned research state | The project is a Git repository; every run's trail and workflow/ files are committed with it |
| ARA (Agent-Native Research Artifact) | The paper as an executable, verifiable artifact | 论文工具包 |
| AutoResearchClaw, NanoResearch, freephdlabor, EvoScientist, Scholar Loop, Microsoft ResearchStudio, Dolphin, SciMaster, Tencent Hyra, Imbue Catalyst | Idea-to-paper pipelines | The task list with 无人值守研究 |
| AutoR, Dr. Claw, ScholarAIO, K-Dense BYOK, Wisp, open-science, OpenScience, OpenAI4S, Orkas, Claude Scientific Skills, Academic Research Skills, Qinyan, Research Agent Skills, GDM Science Skills | Local research workbenches and skill libraries | Auto Research itself, an OpenProgram app; OpenProgram's skills reach every task page's agent |
| Academia Co-scientist, WisPaper, Elsevier ScienceDirect AI / Scopus AI, Wolfram Research Assistant | Commercial research assistants | 领域综述, 想法生成, task conversations |
| PPTAgent, Auto-Slides, paper2slides, SlideDeck AI, Paper2All | Paper to slides, site, poster and media | 海报与报告材料 (slides and poster), 项目主页, 播客与讲解视频 |
| MARG, DeepReview, TreeReview, ReviewAgents, MAMORX, LLM-Peer-Review | Review generation | 论文评审 |
| AiraXiv | Archive of AI-generated research | 研究地图 shares findings across the author's projects |
| Scideator | Papers split into facets and recombined | 组合式想法 (facet_ideas) |
| PaperFold | Five zoom levels from overview to verbatim | 论文精读 (deep_read) |
| Nujan | Step-by-step breakdown of math derivations | 论文精读 derivations |
| CiteSee | Citations marked by your reading and citing history | 论文精读 reference list: cited, read, new |
| Stanford Virtual Lab | The human as PI in meetings with an agent team and a critic | 组会讨论 (lab_meeting) |
| Ax / BoTorch, Optuna | Bayesian-optimisation experiment planning | 超参数搜索 (hparam_sweep) |
| PaperSpine | Style and motivation learned from the author's own papers | 写作风格 (writing_style) |
| SciWrite (Writing in the Sciences) | Manuscript critique by the course's rules | 写作风格 rules |
| gpt_academic | Whole LaTeX paper translated and recompiled | 论文翻译 (paper_translate) |
| PaperBanana, AutomaTikZ, TikZero, StarVector | Method illustrations, text to editable TikZ | 方法示意图 (method_diagram) |
| Scicapenter | Caption writing help | 图表标题检查 (caption_check) |
| Flint, SciencePlots, tikzplotlib | Publication-style plots | 图表标题检查 plotting-script checks |
| HLM-Cite, newborn-article impact prediction | Citation forecasting | 引用预测 (impact_forecast) |
| Academic Commercialization Agent | Technology readiness and patent landscape | 技术转化评估 (tech_transfer) |
| Web of Science Research Assistant (Clarivate) | Search strategy for a review, topic exploration, journal suggestions | 领域综述, 选投会议 |
| Elsevier LeapSpace (was ScienceDirect AI / Scopus AI) | Reading assistant, paper comparison, collaborators, writing coach, retractions excluded | 论文精读, 文献对比表, 写作风格, 引文图谱 retraction notices |
| Springer Nature Curie (AJE) | Language editing for non-native authors as tracked changes | 论文校对 edits, 写作风格 |
| Wiley AI Gateway | The institution's subscribed full text handed to agents | 文本重合检查 reads the author's own PDFs (workflow/papers/, Zotero storage) and open-access copies |
| Wiley Atypon AI Companion, Scholar PDF Reader, Moonlight (Corca) | Summaries, outline and Q&A on the reading page, inline reference jumps, translation | 论文精读 read.html; 论文翻译 |
| Google Scholar Labs | Question split into sub-topics, each paper's relevance explained | 领域综述 research lines and screening reasons |
| ChatGPT for Academic Researchers | Science skills, code execution for research | Task conversations with OpenProgram's skills and tools |
| Dimensions Research GPT | Papers, grants, patents and trials in one search | 领域综述, 基金申请书, 技术转化评估 |
| Sakana Marlin | Hours-long autonomous research report | 领域综述, 无人值守研究 |
| Liner Scholar | Hypotheses and citation suggestions inside a project, conference quick review | 想法生成, 逐段文献推荐, 论文评审 |
| ClawsGO Science | Question to cited paper, cloud lab, own GPU by SSH | The task list; 远程运行 |
| Bohrium 科学空间 / Science Navigator | Desktop research space, papers and patents search | 领域综述, 技术转化评估, 远程运行 |
| 夸克学术, 万方 AI, 百度学术 AI, 阿里心流, AMiner | Chinese full-text search, reading, outlines, paper graphs | 领域综述, 论文精读, 引文图谱 |
| 千笔 AI, 毕业之家 | Chinese thesis writing from topic to layout | 章节计划, 论文校对, 论文翻译 |
| 深度原理 Mira | Materials-science AI scientist (single domain) | Domain tools connect through MCP; the task list is the same |
| Potato (Tater) | Protocol generation, lab robots (wet lab) | 实验计划; wet-lab robots are outside one author's machine |
| Anara (was Unriddle), Paperguide, R Discovery, Scholarcy, Trinka, thesify | Library Q&A, extraction tables, summaries, language checks, mentor-style feedback | Task conversations, 文献对比表, 论文校对, 论文评审 |
| Manusights | Desk-reject risk, journal fit, citation existence and retraction checks | 论文评审, 选投会议, 参考文献核查, 引文图谱 |
| Scholantic, LabForge, Skriptr, PapercrY, PaperFlow, Chirpz | Writing workspaces with research, review and drafts | The task list |
| BioRender AI | Editable schematic figures from text | 方法示意图 |
| Julius AI | Data analysis by conversation | 数据探索, 共识分析 |
| Grammarly Authorship | Replayable record of typed, AI-generated, AI-edited and pasted text | 写作溯源 (writing_provenance) |
| ResearchHub | Paid named human reviewers, AI editor | 投稿前找人审读 (peer_feedback) finds conflict-free readers and drafts requests; 论文评审 for the AI review |
| 腾讯 ima | Side-by-side PDF translation, mind maps, podcasts, quizzes, traceable answers over a knowledge base | 论文精读, 论文翻译, 播客与讲解视频, 研究地图; task conversations cite the project's files |
| 橙篇, WPS AI 论文 | Long-form drafting | 章节计划's agent drafts along the plan card |
| The Academic Digest, ArXivGPT, Seaml.es | Weekly literature digest by email | 新工作跟踪 and 新工作排序 on a campaign schedule |
| Acurio, ReferenceChecker.ai, SummarizePaper | Fake and wrong citations caught | 参考文献核查 |
| ZiNote | Swipe to rate papers | 新工作排序 learns from the author's decisions in watch.csv |
| ScholarThread, Avidnote, Logically, OpenRead, bundleIQ, Wonders AI, Papers, Yila, AnswerThis, Literfy, Scrivy, Sourcely, Litero, Gatsbi, SciPub, Vicena, Orchestra Research, StudyTexter, Paperly, Kinetik, Blainy, Bearly | Literature search, notes, Q&A, review drafts | 领域综述, 文献对比表, task conversations |
| Slashspace (was RabbitHoles AI) | Visual research canvas | 研究地图 |
| PDFdigest | Short explainer video of a paper | 播客与讲解视频 |
| thematicanalysis.ai, NVivo / ATLAS.ti / MAXQDA AI coding | Codes and themes from interview transcripts | 质性主题分析 (thematic_analysis) |
| ByteDance Seed-Prover, DeepSeek-Prover, Harmonic Aristotle | A model proves, Lean checks, errors drive the next attempt | 定理证明 (lean_prove) |
| Nexlit, Citaria, Farside Research, GraceLitRev, EssentAI, ResearchWize, UpSum | Paper library Q&A, linked deep research, structured article breakdown, review workspaces | 领域综述, 文献对比表, 论文精读, task conversations |
| Snowball | Citation snowballing from a core set | 引文图谱, 领域综述 snowballing |
| Scinapse, OA.mg, BASE, Internet Archive Scholar | Academic search engines | 领域综述 |
| Sciwand, Afforai, Citavi, Mendeley, linXiv, BibKit, Web Highlights | Reference libraries and highlights | the import_library and import_annotations operations, 参考文献核查 |
| Nubint AI, SimpleFormat Pro, WordfixerBot, Reword, Strokes, Tweaks | Drafting with verified citations, citation styles, paraphrase | 章节计划, 逐段文献推荐, 参考文献核查, 换投会议, 论文校对 |
| Curasynth | Sample-size and power calculation before running | 种子数功效分析 (power_analysis) |
| Curasynth | Ethics, availability, contribution, interests and funding statements | 论文声明 (paper_statements) |
| Proposia | Open-call search with fit scoring; requirements checklist from the call document | 基金项目检索 (funding_calls), 申请要求清单 (call_requirements) |
| Hyperresearch | Independence audit of supporting sources | 支持文献独立性 (source_independence) |
| DeepGit, Papers with Code | Official code of each baseline | 基线代码 (baseline_code) |
| Submission Copilot, Paper Copilot | Readiness score, venue match, reviewer suggestions, per-venue review statistics | 论文评审 with OpenReview anchors, 选投会议, 投稿前找人审读 |
| ResearchGate, Academia.edu, ResearchCollab, Scholantic, Pho Chat | Sharing, impact tracking, team workspaces, medical cited answers | 发表后跟踪, 项目主页, the task list, 主张与文献对照 |
| FOREST, FAROS 智塔, Luxas, DARE, AutoScholarLoop, Research Toolkit, WriteHERE | Branchable research graph, research contract and claim audit, linted figures, approved plans, recursive long-form writing | 研究地图, 实验计划, 实验证据, 论文图表, 无人值守研究, 章节计划 |
| Tongyi DeepResearch, MiroThinker, deep-searcher, HKUDS Auto-Deep-Research, OpenResearcher, NVlabs UniversalDeepResearch, Fathom-DeepResearch, smolagents open_Deep-Research, open-ai-co-scientist | Open deep-research and co-scientist agents | 领域综述, 想法生成 |
| Agent Bayes, ClaimWeft, Clarami AI, Grongy AI | Inspectable cited arguments, evidence records with conflicting findings side by side, evidence-based thesis workspaces | 研究地图, 主张与文献对照, 章节计划, 领域综述 |
| Authorea, HackMD, Inkwell, AINGENS MACg | Collaborative documents with live data and equations, medical-affairs writing | the project's Git repository, 论文图表, 章节计划 |
| GRADEpro, DistillerSR, Rayyan, Dr.Oracle | Pooled effects, certainty of evidence, dual blind screening, evidence strength | 荟萃分析 (meta_analysis) |
| Prolific, Branded, Qvantify, Listen Labs | Human evaluation studies and automated interviews | 人工评测问卷 (human_eval_build), 人工评测分析 (human_eval_analyze); interviews analysed by 质性主题分析 |
| DeepWiki | Module documentation and Q&A for a code repository | 代码 Wiki (code_wiki) |
| Seamless, ResearchBuddy, SciReviewHub, ResearchAIde, Cambrian, Iris.ai, wizdom.ai, AcademicID, Synthical, Exa, Tavily / GPT Researcher | Automated search, screening and review drafts; research intelligence | 领域综述, 选题趋势 |
| QuillWord, Samwell.ai, WeGuru, SciPub+, Isaac, Devana, SOM AI, Claude Scholar, Aithor, Instaphrase, Wisio, Citeclops | Academic drafting, paraphrase, auto-citation, citation format | 章节计划, 论文校对, 逐段文献推荐, 文本重合检查, 参考文献核查 |
| ChatPDF, ScholarTurbo, PDF AI, pdfy, DocGPT, PDFChat, PaperLens, PaperPilot, Bohr Chat, Layer, Upword, BenAI, Merlin, TXYZ, Paper Visualizer | Chat with and summarize PDFs, knowledge-graph view | task conversations, 论文精读, 研究地图 |
| Zeli, GoatStack, ArXiv Pulse, ArxivPaperAI, Google Illuminate, PaperTalk, PaperClip, Glasp | arXiv digests, audio discussions, paper communities, highlights | 新工作跟踪, 新工作排序, 播客与讲解视频, 发表后跟踪, the import_annotations operation |
| Paperade, Tech2Transfer, The Science App, Fact Check Anything, MediSearch, Lime, Packback | Commercial use, pro/contra evidence, fact checks, data analysis, writing feedback | 技术转化评估, 主张与文献对照, 数据探索, 写作风格 |
| Delve, Dovetail, Marvin, Recollective, XLSTAT, JMP, Mathematica | Qualitative coding, statistics | 质性主题分析, 数据探索, 显著性检验 |
| scienceOS, Inquisite, Find My Papers AI, Refseek, PubMed, Tokino, WickIQ, AgentClara, Zeta Alpha, ResoluteAI | Search and chat over scholarly, regulatory and patent databases | 调研材料, 领域综述, 技术转化评估 |
| PapersFlow, Resea.AI, CopyOwl.ai, Ai Intern, Alpha Inquire, Genspark, Skywork Super Agents, Juno, Cresh, Edison Analysis | Autonomous literature reviews, reports and data analysis | 领域综述, 无人值守研究, 共识分析, 数据探索 |
| Noah AI, BenchSci, Reliant AI, biologit MLM-AI, PubHive Navigator | Life-science literature monitoring and curation | 新工作跟踪, 文献对比表 |
| genei, Long Summary, TLDRly, iAsk, Sider, Filechat, AskDocs, Mneme AI, PaperChat, Sharly, Skipit, StudyPDF, SpeakDocs, aiPDF, Typelets, Ponder AI, myReach, Sorc'd | Summaries, chat with PDFs, knowledge canvases | 论文精读, task conversations, 研究地图, 研究笔记 |
| JotBot, PaperGen, Scifocus, FlashPaper, Essaybot, ScienHub, Smodin, Wordtune, ProWritingAid, Paraphraser.io | Drafting, LaTeX editing, paraphrase and grammar | 章节计划, 论文校对, 写作风格 |
| Copyleaks, Quetext, Noplag, PlagiarismSearch, Plagly, DupliChecker, Check-Plagiarism, CopyChecker, GPTZero, ZeroGPT, Sapling, Illuminarty, Zhuque, AI Detector Pro | Text overlap and AI-text detection | 文本重合检查, 写作溯源, AI 使用声明 |
| Codequiry, Software Heritage, ScanCode, REUSE | Copied code, its origin and license | 代码来源与许可证核查 (code_origin) |
| EndNote, RefWorks, Paperpile, Sciwheel, JabRef, BibDesk, Bookends, Biblioscape, Qiqqa, Docear, Referencer, Citationsy, EasyBib, MyBib, Cite This For Me, NoodleTools, Koke AI | Reference libraries and citation formatting | the import_library and import_annotations operations, 参考文献核查 |
| figshare, Zenodo GitHub integration, Citation File Format | Citation metadata and an archive DOI for released code | 软件引用文件 (software_citation) |
| BibBase, Google Scholar profiles, academicpages | The author's publication list | 论文列表 (publication_list) |
| Quirkos, CATMA, AQUAD, Aurelius, Notably, Reveal, Validly, Ripple Science | Qualitative coding, participant recruitment | 质性主题分析, 人工评测问卷 |
| PaperDelta, Agent-Cockpit, Beyond fabrication (arXiv 2610.38691) | The paper's own numbers checked against the runs that produced them; stale numbers and derived claims after a rerun | 结果数字核对 (result_numbers) |
| FirstResearch, Idea2Prior, RATIO, neuroarxiv, DeepInstructor, rTaste, IdeaTaste, AlphaIdeaBench, Ideation Arena, hypoarena | Research-question certificates, prior-work search for an idea, idea judging | 想法与研究问题, 想法生成 |
| MLE-World, Research World Models, AI Research Preference Models, EEM, DAGLoop, PrimeScientist, JANUS, Epi, YouRA, Inventa, Sage-Taiyi, JevGPT, Between-Round Decision, AIMC | Predicting results before running, autonomous research controllers and dashboards | 实验循环, 无人值守研究 |
| Study Contracts, ABE-Ralph, RIDAR, Discovery Certification Protocol | Preregistration, experiment fidelity and integrity audits | 实验计划, 实验设置核对, 实验方法检查, 干净环境复现 |
| CrossAudit, AppliedScientist, ActReview, PaperJury, aaai-review-simulator, Moa, Acceptodds, PremiseCheck | Cross-vendor review, review-revise loops, acceptance odds, checking reviewers' premises | 论文评审, 论文校对, 引用预测, 审稿回复 |
| SciFigAlign, Fig0, SciFigureAI, TikZ Editor, editaplot, NaturePanelForge, FigGenie, opentikz, image2editable-ppt | Figures that support the text, figure and TikZ generation | 论文图表, 图表标题检查, 方法示意图 |
| PARIS, ReviewPilot, CoSLR, Optimist Deep Research, Deep Academic Survey, SurveyAgent-HKA, SmoothScore, CESS, Mole, Apodex, MIRA, LINEAGE | Systematic and deep literature reviews, coverage and evidence-selection audits, baseline lineage | 领域综述, 支持文献独立性, 漏掉的更强基线 (missing_baselines), 逐段文献推荐 |
| PreFlight, VerusCite, Quoted, CiteGuard, Citeparse, Scholar Sidekick, paper-preflight, Back Me Up, EBM Lens, Sci-Trust | Citation and claim checking | 参考文献核查, 主张与文献对照, 逐段文献推荐, 支持文献独立性 |
| KnowMe Paper Radar, paper-radar, Papertrails, Agora, Symposium, rStation, ZeroThesis, ScholarStack, Hypothesis Prism | Paper radars, research canvases | 新工作排序, 新工作跟踪, 研究地图 |
| Unglazed, ArXivMax, Flashpapers, paper2anything, academic-ppt-master, speaker, AutoDesign | Audio, slides and talks from papers | 播客与讲解视频, 海报与报告材料, 报告问答准备 |
| Agentero, PaperRead, nodus, easyread, NeoRead, textfold, PaperForge, paper-xray, TeXbrain, Aldine, Papiers.ai, Panel, Oleafly, papilot, lattice | Paper reading and translation, LaTeX workspaces | 论文精读, 论文翻译, the project's Git repository, task conversations |
| anti-defensive-writing, oral-paper-skill, SciPilot, ieee-skills, Best-Paper-Writing, PaperPush, Paper-Rebuttal-Tips, Athena academic page | Writing style, submission packaging, rebuttal tips, academic pages | 写作风格, 论文校对, 投稿表单信息, 投稿前检查, 审稿回复, 论文列表 |
| EvoMap AutoResearch, SoL-Pi, spark-to-paper, Light-Science, Draftpaper_loop, Argus, Polaris, ScienceBuddy, ResearchPilot, ai4s-skills, openJiuwen ScienceDiscovery, AI-Scientist-v3 | End-to-end research agents | the task list |

## How the list was made

The products above are the union of these lists, read on 2026-11-24, with benchmarks, model
weights, wet-lab and single-domain agents, venue-side tools and general agent frameworks left
out: ai-boost/awesome-ai-for-science, ResearAI/Awesome-AI-Scientist, openags/Awesome-AI-Scientist-Papers,
natnew/awesome-ai-scientists, LightChen233/Awesome-AI4Research, AgenticScience/Awesome-Agent-Scientists,
zkzhou126/AI-for-Research, zhoujieli/awesome-llm-agents-scientific-discovery,
DavidZWZ/Awesome-Deep-Research and Demfier/awesome-deep-research (on GitHub), the companion
surveys AI4Research (arXiv 2507.01903), From AI for Science to Agentic Science (2508.14111),
From Hypothesis to Publication (2503.01424) and How Far Are AI Scientists from Changing the
World? (2507.23276), the research categories of futurepedia, Product Hunt and theresanaiforthat
(academic research, literature review, scientific writing, research papers; read in a browser), publishers' and large vendors' 2025–2026 launches, and the
Chinese, Korean and Japanese markets by web search; then AlternativeTo (alternatives to Elicit, Consensus,
SciSpace, Scite, Paperpal, Semantic Scholar, Zotero), aitools.fyi, Hugging Face Spaces and the GitHub topics
ai-scientist, deep-research and research-agent (over 300 stars).

Saturation check, 2026-11-24: the newest sources not used above were swept last: 448 arXiv papers
of 2026-07 to 2026-11 (AI scientist, research agent, deep research, automated discovery, LLM peer
review, paper and scientific writing agents, literature review agents), 1,758 Show HN posts of 2026,
629 Product Hunt launches of August to November 2026 (official RSS and Wayback feeds) and 247
GitHub repositories created after 2026-06-01 with over 200 stars. About 110 new names turned up;
all but one capability were already covered, and that one (the paper's own numbers against the
runs) became 结果数字核对; LINEAGE's search for stronger uncited baselines, which 基线数字核对 covered only for
cited works, became 漏掉的更强基线. The last four rounds found 5, 3, 3 and
then 1 gaps among about 40, 110, 110 and 110 new names: new products now repeat covered features.
Anti-detection rewriters (Turnitout, academic-humanizer and the like) stay out with the 降AI率
services.

Not built on purpose: tools that lower an AI-detection score (笔灵 and other 降AI率 services) hide
AI use instead of disclosing it; 写作溯源 and AI 使用声明 disclose it. G2, Toolify, SaaSworthy and Capterra refuse
automated reading, so their category pages were read from Wayback Machine snapshots of 2025–2026
(G2 collaborative research writing and AI writing assistants; Toolify literature review, research
assistant, academic research and AI papers); SaaSworthy's and Capterra's research categories have no snapshot, so
the same listings were read from SaaSHub's alternatives pages and the Slashdot and SourceForge
directories (academic research, AI research agents and tools, AI science, reference management,
research management, plagiarism, AI detection, chat with PDF, summarizers, lab notebooks, qualitative
analysis; Wayback snapshots of 2025–2026), plus GetApp's and Software Advice's qualitative analysis lists; the research tools G2 files under AI Writing Assistants and
Collaborative Research Writing (AnswerThis, ResearchPal, Ontosight.ai, Paperguide, SciSpace) are
covered above.
