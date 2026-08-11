# OpenProgram Homepage Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the sparse landing page with an evidence-backed, responsive product homepage and complete its search and sharing metadata.

**Architecture:** Keep the homepage as one dependency-free `site/index.html`. Reuse assets already published by the docs build under `/docs/`, and add one stdlib validation script for structural regression checks.

**Tech Stack:** HTML, CSS, vanilla JavaScript, Python standard library, existing docs-site builder

## Global Constraints

- Preserve the selected near-black, teal, and violet direction.
- Use only claims already supported by `docs/README.md` and `docs/start/GETTING_STARTED.md`.
- Use the documented installer rather than `pip install openprogram`.
- Add no dependency or external font request.
- Keep all content visible when JavaScript is unavailable.

---

### Task 1: Add the landing-page regression check

**Files:**
- Create: `tools/docs_site/check_landing.py`

**Interfaces:**
- Consumes: `site/index.html`
- Produces: exit code 0 only when the homepage contains required metadata, sections, installer, images, and no CSS that hides content

- [ ] Write assertions for canonical and social metadata, JSON-LD, section ids, the documented installer, three existing screenshot paths, favicon, and visible content.
- [ ] Run `python -m tools.docs_site.check_landing` and verify it fails against the current page.

### Task 2: Rebuild the landing page

**Files:**
- Modify: `site/index.html`

**Interfaces:**
- Consumes: `/docs/assets/mark.svg`, `/docs/images/code_hero.png`, `/docs/images/chat_hero.png`, `/docs/images/tui_hero.png`, and existing documentation URLs
- Produces: dependency-free responsive homepage at `/`

- [ ] Add complete metadata and JSON-LD.
- [ ] Implement the approved information structure and accurate installation copy.
- [ ] Keep content visible and make the ambient hero motion reduced-motion safe.
- [ ] Run `python -m tools.docs_site.check_landing` until it passes.

### Task 3: Build, inspect, and publish

**Files:**
- Modify only if verification finds a defect: `site/index.html`, `tools/docs_site/check_landing.py`

**Interfaces:**
- Consumes: finished homepage and current docs builder
- Produces: verified local and deployed homepage

- [ ] Run the docs build, docs link checker, landing checker, and `git diff --check`.
- [ ] Render desktop and mobile screenshots and correct visible defects.
- [ ] Commit only the homepage design, plan, check, and HTML changes.
- [ ] Push `main`, wait for `Publish openprogram.io`, and verify the live root page and referenced assets.
