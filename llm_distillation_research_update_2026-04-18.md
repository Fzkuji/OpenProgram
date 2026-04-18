# LLM Distillation Research Update

Date: 2026-04-18

This memo extends the existing literature review in `/Users/fzkuji/Documents/LLM Distillation/literature review` based on the current local corpus plus recent primary-source papers through April 2026.

## 1. Current corpus status

From the existing local review:

- 4 surveys
- 56 papers, all annotated
- Strongest coverage:
  - white-box logit/feature KD
  - black-box CoT distillation
  - on-policy distillation
  - reasoning distillation
- Weak or missing coverage:
  - agent / tool-use / RAG distillation
  - evaluation and benchmarks
  - quantifying distillation level
  - interpretability / mechanistic analysis
  - multimodal distillation methods
  - structural compression combined with distillation
  - multi-teacher / difficulty-aware / distribution-aware KD as explicit sub-axes

## 2. Main diagnosis

The current review is already usable as a paper-collection project, but it is not yet organized tightly enough to support a full survey article.

The biggest issue is no longer "paper count"; it is taxonomy quality.

Three structural problems stand out:

1. The framework under-represents 2025-2026 method shifts.
   Recent work is moving beyond plain KL / RKL / JSD and beyond plain teacher-generated SFT data. The active frontier now includes:
   - difficulty-aware data scheduling
   - distribution-aware reweighting
   - residual / error-aware transfer
   - direct relative-logit or score-matching objectives
   - multi-teacher conflict resolution

2. The framework is missing an entire "LLM compression + KD" branch.
   Your README explicitly includes pruning + distillation and quantization + distillation, but the current topic tree does not have a dedicated branch for this. That mismatch will become a problem when writing a "comprehensive" survey.

3. The evaluation branch is too weak to support a convincing survey conclusion.
   A mature survey cannot stop at methods. It needs a section on:
   - what to evaluate
   - how to evaluate
   - how to measure actual inheritance rather than benchmark coincidence
   - whether distillation homogenizes behavior

## 3. Priority additions to ingest next

Below are the highest-value additions I would add to the local review first.

### A. Data-aware / adaptive white-box KD

1. **DA-KD: Difficulty-Aware Knowledge Distillation for Efficient Large Language Models**
   - Status: ICML 2025 poster, published May 1, 2025
   - Why it matters: reframes KD efficiency as a data scheduling problem, not only a loss-design problem
   - Suggested placement:
     - `LLM Distillation/Training Paradigms: Off-Policy, On-Policy & Self-*/Off-Policy SFT on Teacher Outputs`
     - `LLM Distillation/Dataset Distillation & Synthetic Data/Data Selection, Filtering & Coreset`
   - Link: https://openreview.net/forum?id=NCYBdRCpw1&noteId=FUdWhE3vJM

2. **Discrepancy-Aware Knowledge Distillation for Large Language Models**
   - Status: ICLR 2026 submission, posted September 18, 2025
   - Why it matters: pushes KD toward distribution-aware reweighting by comparing teacher vs base-teacher discrepancy
   - Suggested placement:
     - `LLM Distillation/Foundations & Preliminaries/Divergences, Exposure Bias & Theoretical Foundations`
     - new leaf: `Adaptive / Data-aware Distillation`
   - Link: https://openreview.net/forum?id=nkm3lL8CQE

3. **Knowledge Distillation for Large Language Models through Residual Learning**
   - Status: ICLR 2026 poster, published January 26, 2026
   - Why it matters: explicitly treats teacher errors as a problem and supports cross-tokenizer distillation
   - Suggested placement:
     - `LLM Distillation/White-box Distillation/Hint / Feature-based KD`
     - `LLM Distillation/Foundations & Preliminaries/KD Problem Setting for LLMs`
   - Link: https://openreview.net/forum?id=Dh6KxUxG20

4. **Distillation of Large Language Models via Concrete Score Matching**
   - Status: ICLR 2026 poster, published January 26, 2026
   - Why it matters: a clean post-softmax alternative that distills relative logit geometry directly
   - Suggested placement:
     - `LLM Distillation/White-box Distillation/Logit-based KD`
     - `LLM Distillation/White-box Distillation/Sequence-level White-box KD`
   - Link: https://openreview.net/forum?id=bZBJFrxH1H

### B. Multi-teacher and conflict resolution

5. **Exploring Knowledge Purification in Multi-Teacher Knowledge Distillation for LLMs**
   - Status: ICLR 2026 poster, published January 26, 2026
   - Why it matters: multi-teacher KD is no longer a side note; this paper directly addresses rationale conflicts and routing
   - Suggested placement:
     - new leaf: `Multi-Teacher Distillation`
     - `LLM Distillation/Black-box Distillation/Chain-of-Thought / Rationale Distillation`
   - Link: https://openreview.net/forum?id=7pvJoB4aKO

6. **MAGDi: Structured Distillation of Multi-Agent Interaction Graphs Improves Reasoning in Smaller Language Models**
   - Status: ICML 2024 poster, published May 2, 2024
   - Why it matters: shows that multi-agent interaction structure itself can be distilled, not just final answers or plain CoT
   - Suggested placement:
     - `LLM Distillation/Skill, Task-Agnostic vs Task-Specific & Vertical Distillation/Reasoning Distillation`
     - `LLM Distillation/Skill, Task-Agnostic vs Task-Specific & Vertical Distillation/Agent, Tool-Use & RAG Distillation`
   - Link: https://openreview.net/forum?id=ffLblkoCw8

### C. Agent / tool-use / RAG distillation

7. **Sub-Goal Distillation: A Method to Improve Small Language Agents**
   - Status: CoLLAs 2024, arXiv May 4, 2024
   - Why it matters: one of the clearest early agent-distillation papers; decomposes planning and execution
   - Suggested placement:
     - `LLM Distillation/Skill, Task-Agnostic vs Task-Specific & Vertical Distillation/Agent, Tool-Use & RAG Distillation`
   - Link: https://arxiv.org/pdf/2405.02749

8. **Distilling LLM Agent into Small Models with Retrieval and Code Tools**
   - Status: NeurIPS 2025 spotlight, published September 18, 2025
   - Why it matters: moves from reasoning-only distillation to full agent behavior transfer with tools
   - Suggested placement:
     - `LLM Distillation/Skill, Task-Agnostic vs Task-Specific & Vertical Distillation/Agent, Tool-Use & RAG Distillation`
     - `LLM Distillation/Black-box Distillation/Chain-of-Thought / Rationale Distillation`
   - Link: https://openreview.net/forum?id=VkicTqszOn

9. **AgentDistill: Training-Free Agent Distillation with Generalizable MCP Boxes**
   - Status: ICLR 2026 submission, posted September 19, 2025
   - Why it matters: shows a training-free path for agent distillation through reusable protocol modules
   - Suggested placement:
     - `LLM Distillation/Skill, Task-Agnostic vs Task-Specific & Vertical Distillation/Agent, Tool-Use & RAG Distillation`
   - Link: https://openreview.net/forum?id=UfSbS4N1ob

10. **Democratizing Agentic RAG: Distillation-Guided Policy Optimization for Compact Language Models**
    - Status: NeurIPS 2025 LAW workshop, published September 23, 2025
    - Why it matters: directly bridges distillation and RL for compact agentic RAG, and introduces a task-specific evaluation view
    - Suggested placement:
      - `LLM Distillation/Skill, Task-Agnostic vs Task-Specific & Vertical Distillation/Agent, Tool-Use & RAG Distillation`
      - `LLM Distillation/Training Paradigms: Off-Policy, On-Policy & Self-*/RL- and Ranking-based KD`
    - Link: https://openreview.net/forum?id=CP0H9NAWES

### D. Alignment / preference distillation

11. **Advantage-Guided Distillation for Preference Alignment in Small Language Models**
    - Status: ICLR 2025 spotlight, published January 22, 2025
    - Why it matters: this should be a core paper in your alignment-distillation leaf; it is more directly on-point than leaving that leaf near-empty
    - Suggested placement:
      - `LLM Distillation/Skill, Task-Agnostic vs Task-Specific & Vertical Distillation/Alignment & Preference Distillation`
      - `LLM Distillation/Training Paradigms: Off-Policy, On-Policy & Self-*/RL- and Ranking-based KD`
    - Link: https://openreview.net/forum?id=xsx3Fpo3UD

### E. Multimodal distillation

12. **A Framework of Distilling Multimodal Large Language Models**
    - Status: ICLR 2025 submission, posted September 14, 2024
    - Why it matters: a direct MLLM teacher-student framework with multimodal output and relation distillation
    - Suggested placement:
      - `LLM Distillation/Skill, Task-Agnostic vs Task-Specific & Vertical Distillation/Multi-Modal Distillation`
    - Link: https://openreview.net/forum?id=MFySy0DWAH

13. **CompoDistill: Attention Distillation for Compositional Reasoning in Multimodal LLMs**
    - Status: ICLR 2026 poster, published January 26, 2026
    - Why it matters: identifies visual attention misalignment as a concrete MLLM-KD bottleneck
    - Suggested placement:
      - `LLM Distillation/Skill, Task-Agnostic vs Task-Specific & Vertical Distillation/Multi-Modal Distillation`
      - `LLM Distillation/White-box Distillation/Hint / Feature-based KD`
    - Link: https://openreview.net/forum?id=Wa9Bg9b50B

### F. Evaluation / benchmark / interpretability

14. **The Quest for Efficient Reasoning: A Data-Centric Benchmark to CoT Distillation**
    - Status: ICLR 2026 poster, published January 26, 2026
    - Why it matters: fills your currently empty evaluation branch and turns data selection / augmentation into a benchmarkable question
    - Suggested placement:
      - `LLM Distillation/Evaluation, Scaling & Open Problems/Benchmarks & Robustness Evaluation`
      - `LLM Distillation/Dataset Distillation & Synthetic Data/Data Selection, Filtering & Coreset`
    - Link: https://openreview.net/forum?id=Dud8FtScW7

15. **Towards Understanding Distilled Reasoning Models: A Representational Approach**
    - Status: BuildingTrust workshop, published March 5, 2025
    - Why it matters: one of the few papers directly asking what distillation changes internally in reasoning models
    - Suggested placement:
      - `LLM Distillation/Evaluation, Scaling & Open Problems/Interpretability of Distilled LLMs`
    - Link: https://openreview.net/forum?id=UYZCcnwgc4

16. **When Agents Look the Same: Quantifying Distillation-Induced Similarity in Tool-Use Behaviors**
    - Status: ACL ARR 2026 January submission, posted January 5, 2026
    - Why it matters: one of the clearest candidates for your currently empty `Quantifying Distillation Level` leaf
    - Suggested placement:
      - `LLM Distillation/Evaluation, Scaling & Open Problems/Quantifying Distillation Level`
      - `LLM Distillation/Evaluation, Scaling & Open Problems/Benchmarks & Robustness Evaluation`
    - Link: https://openreview.net/forum?id=7cT7yBjr9i

### G. Structural compression + KD

17. **Compact Language Models via Pruning and Knowledge Distillation**
    - Status: arXiv July 19, 2024
    - Why it matters: this is the cleanest direct evidence that pruning + KD should be a first-class branch in the survey
    - Suggested placement:
      - new branch: `Structure-Aware Compression + Distillation/Pruning + Distillation`
      - `LLM Distillation/Evaluation, Scaling & Open Problems/Scaling Laws, Compression Ratios & Industrial Deployment`
    - Link: https://arxiv.org/pdf/2407.14679

18. **LBLLM: Lightweight Binarization of Large Language Models via Three-Stage Distillation**
    - Status: ACL ARR 2026 January submission, posted December 29, 2025
    - Why it matters: quantization-aware distillation at extreme low-bit settings is now active enough to deserve its own survey subsection
    - Suggested placement:
      - new branch: `Structure-Aware Compression + Distillation/Quantization + Distillation`
    - Link: https://openreview.net/forum?id=AE6IfwOhEb

## 4. Taxonomy changes I recommend

If the goal is to eventually write a publishable survey, I would refactor the topic tree as follows.

### Keep

- Foundations & Preliminaries
- White-box Distillation
- Black-box Distillation
- Training Paradigms
- Dataset Distillation & Synthetic Data
- Skill / Domain Distillation
- Evaluation, Scaling & Open Problems

### Add

1. **Adaptive and Multi-Teacher Distillation**
   - Difficulty-aware KD
   - Distribution-aware KD
   - Multi-teacher / routing / purification
   - Uncertainty-aware KD

2. **Structure-Aware Compression + Distillation**
   - Pruning + KD
   - Quantization + KD
   - Architecture search / student design
   - Distillation for sparse / efficient / low-bit students

3. **Agentic Distillation**
   - Tool-use distillation
   - RAG / agentic search distillation
   - Planning / sub-goal distillation
   - Training-free agent distillation

### Re-route some existing papers

- DPKD, BOND, SPPO, and related preference-transfer papers should not live only under RL/ranking KD.
  They should also populate `Alignment & Preference Distillation`.

- Some reasoning papers currently clustered under CoT should be split more carefully into:
  - rationale transfer
  - verifier / reward-guided distillation
  - multi-agent reasoning distillation

- `Multi-Modal Distillation` should contain actual method papers, not mainly generic surveys.

## 5. Survey-level synthesis: the stronger narrative

A stronger survey thesis is:

> LLM distillation has shifted from "compressing a model" to "transferring a capability stack" under partial teacher access, data bottlenecks, and deployment constraints.

That immediately gives you a clearer macro-structure:

1. **What is being transferred?**
   - logits
   - features
   - sequences
   - rationales
   - preferences
   - tool-use trajectories
   - synthetic datasets

2. **How is it transferred?**
   - off-policy imitation
   - on-policy teacher feedback
   - ranking / preference optimization
   - self-distillation
   - multi-teacher purification
   - adaptive data selection

3. **What is the deployment target?**
   - generic small LMs
   - domain models
   - reasoning models
   - tool-using agents
   - multimodal LLMs
   - quantized or pruned students

4. **How do we know transfer actually happened?**
   - benchmark accuracy
   - OOD transfer
   - reasoning faithfulness
   - behavior similarity
   - representational change
   - compute-quality tradeoff

This is substantially stronger than a flat white-box / black-box split alone.

## 6. Research gaps worth highlighting in the final survey

These are the gaps that now look genuinely important after combining the local corpus with recent papers:

1. **No unified evaluation for capability inheritance**
   We still lack a standard metric for how much capability is truly inherited instead of benchmark-fit coincidence.

2. **Agent distillation is emerging but fragmented**
   Tool use, planning, memory, retrieval, and code execution are being distilled, but no stable taxonomy has formed yet.

3. **Data curation is becoming as important as loss design**
   Recent papers increasingly show that choosing what to distill may matter as much as how to distill it.

4. **Teacher quality is not guaranteed**
   Newer work explicitly recognizes teacher mistakes, teacher conflict, and teacher-distribution mismatch.

5. **Structural compression and KD are still under-integrated in surveys**
   Yet in practice, deployment often uses KD together with pruning, quantization, or student architecture redesign.

6. **Interpretability is still underdeveloped**
   We know distilled students can match outputs, but we still understand poorly whether they inherit mechanisms, heuristics, or only behavior.

## 7. Recommended next writing move

If you want this to become a real survey paper rather than a paper dump, the next step should not be "search more everywhere."

The next step should be:

1. freeze a revised taxonomy
2. re-route existing 56 papers under the revised taxonomy
3. ingest the 12-18 papers above
4. write a survey skeleton with section-level claims, not just topic folders

The survey skeleton I would write next is:

- Introduction
- Distillation in the LLM era: from compression to capability transfer
- Foundations: access assumptions, granularity, divergences, exposure bias
- Algorithmic families:
  - white-box
  - black-box
  - adaptive / multi-teacher
  - on-policy / RL-based
- Distillation targets:
  - reasoning
  - alignment
  - agents / RAG / tools
  - multimodal
  - vertical domains
- KD with structural compression
- Evaluation and interpretability
- Open problems and future directions

## 8. Bottom line

Your local review already has enough material to stop being "collection-oriented" and become "survey-oriented".

What it still needs is:

- a better taxonomy for 2025-2026 papers
- explicit agent / evaluation / structural-compression branches
- stronger treatment of adaptive data-centric KD
- a real synthesis layer that explains where the field is moving

