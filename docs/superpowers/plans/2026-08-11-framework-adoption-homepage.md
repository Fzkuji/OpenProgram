# Framework Adoption Homepage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refocus openprogram.io on installing and using OpenProgram as a general-purpose agent framework while preserving its verified technical context and related papers.

**Architecture:** Keep the homepage as one dependency-free `site/index.html` document and extend the existing standard-library landing-page validator. Reorder and rewrite existing sections rather than introducing a frontend build system or new assets.

**Tech Stack:** Semantic HTML, responsive CSS, small progressive-enhancement JavaScript, Python standard-library HTML parsing, GitHub Pages.

## Global Constraints

- Retain the existing near-black, teal, and violet visual system and existing local assets.
- Use only product capabilities and installation paths confirmed by repository documentation.
- Keep both related papers and do not mention the KDD workshop.
- Keep content visible without JavaScript and preserve keyboard focus, reduced motion, alternative text, copy fallback, and mobile responsiveness.
- Do not modify or stage unrelated untracked OpenProgram source files.

### Task 1: Specify the Product-First Landing Contract

**Files:**
- Modify: `tools/docs_site/check_landing.py`

**Interfaces:**
- Consumes: `site/index.html` through `LANDING` and `LandingParser`.
- Produces: a failing then passing `python -m tools.docs_site.check_landing` contract.

- [ ] **Step 1: Add failing product-positioning assertions**

Require the section IDs `use-cases`, `quick-start`, `capabilities`, `interfaces`, `mechanisms`, `papers`, and `start`. Require the visible phrases `Build agents that run in Python.`, `Use an agent or build your own.`, `GUI Agent`, `Research Agent`, `Wiki Agent`, and `Everything you need to run agents.` Require both installer commands `curl -fsSL ... | bash` and `openprogram`. Require links to the three harness repositories, and reject `Agents are just Python functions.` plus the workshop name.

```python
for section_id in (
    "use-cases", "quick-start", "capabilities", "interfaces",
    "mechanisms", "papers", "start",
):
    require(section_id in page.ids, f"missing #{section_id} section", failures)

for phrase in (
    "Build agents that run in Python.",
    "Use an agent or build your own.",
    "Everything you need to run agents.",
    "GUI Agent", "Research Agent", "Wiki Agent",
):
    require(phrase in visible_text, f"missing product copy: {phrase}", failures)

require("Agents are just Python functions." not in visible_text,
        "landing page still leads with the concept message", failures)
```

- [ ] **Step 2: Run the contract and confirm failure**

Run: `python -m tools.docs_site.check_landing`

Expected: non-zero exit with missing new sections or product copy.

- [ ] **Step 3: Commit the contract together with Task 2**

Do not commit a deliberately failing main branch. Stage this file only after Task 2 passes the contract.

### Task 2: Implement the Framework Adoption Homepage

**Files:**
- Modify: `site/index.html`
- Test: `tools/docs_site/check_landing.py`

**Interfaces:**
- Consumes: existing `/docs/` routes, `/docs/images/code_hero.png`, `/docs/images/chat_hero.png`, `/docs/images/tui_hero.png`, and the supported install script.
- Produces: a semantic static homepage matching the Task 1 section and copy contract.

- [ ] **Step 1: Replace the concept-first hero**

Use `Open-source agent framework`, `Build agents that run in Python.`, and product copy that names the runtime, Web UI, terminal UI, model connections, tools, skills, memory, and multi-agent collaboration. Link `Install OpenProgram` to `#quick-start` and `Explore documentation` to `/docs/`. Replace the synthetic code terminal with `/docs/images/chat_hero.png` and descriptive alternative text.

- [ ] **Step 2: Add use and build paths**

Add `#use-cases` with links to:

```text
https://github.com/Fzkuji/GUI-Agent-Harness
https://github.com/Fzkuji/Research-Agent-Harness
https://github.com/Fzkuji/Wiki-Agent-Harness
```

Add a fourth build-your-own card containing the existing compact `@agentic_function` triage example and a link to the authoring documentation.

- [ ] **Step 3: Add early Quick Start**

Add `#quick-start` before detailed capabilities. Include the exact supported macOS/Linux installer, `openprogram`, copy controls, and a link to `/docs/install/install.html` for Windows and advanced options.

- [ ] **Step 4: Add included capabilities**

Add `#capabilities` with concise cards for model providers, tools and MCP, skills and memory, multi-agent collaboration, sessions and branches, interfaces, and channel integrations. Describe functions directly and avoid research claims.

- [ ] **Step 5: Reorder supporting material**

Keep real interface screenshots after the capabilities section. Move `#mechanisms` after `#interfaces`, keep `#papers` after mechanisms, and retain the final `#start` installation action. Update navigation and footer anchors to match.

- [ ] **Step 6: Add responsive styles using existing CSS**

Reuse the existing card, grid, button, focus, and reduced-motion patterns. Add only the selectors needed for the new hero image, use-case grid, quick-start commands, and capability grid. Stack new grids at existing 980px and 680px breakpoints.

- [ ] **Step 7: Run static verification**

Run:

```bash
python -m tools.docs_site.check_landing
python -m tools.docs_site.build
python -m tools.docs_site.checklinks
git diff --check
```

Expected: `check-landing: ok`, successful build, `0 broken link(s)`, and no diff errors.

- [ ] **Step 8: Commit the implementation**

```bash
git add site/index.html tools/docs_site/check_landing.py
git commit -m "feat(site): present OpenProgram as an agent framework"
```

### Task 3: Visual Acceptance and Publication

**Files:**
- Verify: `site/index.html`
- Verify: generated GitHub Pages output

**Interfaces:**
- Consumes: the completed static homepage and existing `Publish openprogram.io` workflow.
- Produces: desktop/mobile evidence and live HTML containing the approved product copy.

- [ ] **Step 1: Inspect desktop and mobile renders**

Serve the repository locally and capture a 1440px desktop screenshot and a 390px mobile screenshot. Confirm readable hero copy, visible harness cards, stacked mobile grids, no horizontal clipping, and unchanged keyboard-visible styling.

- [ ] **Step 2: Push the two homepage commits**

Run: `git push origin main`

- [ ] **Step 3: Wait for the publication workflow**

Find the `Publish openprogram.io` run for the implementation commit and wait for a successful conclusion.

- [ ] **Step 4: Verify live content**

After `Fzkuji/openprogram-site` reports `status=built`, fetch the live homepage with a cache-busting query. Require the new hero, all three harness names, both paper IDs, and zero workshop mentions.
