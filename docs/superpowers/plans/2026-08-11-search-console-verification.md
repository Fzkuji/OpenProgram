# Search Console Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish Google's ownership proof at the OpenProgram domain root while retaining the complete build-generated sitemap.

**Architecture:** The proof remains a root-static source file. The existing docs builder copies it into its output, and the GitHub Actions assembly step promotes it from the `/docs/` staging subtree to the final domain root before deployment.

**Tech Stack:** Static HTML, Python documentation builder, GitHub Actions shell workflow

## Global Constraints

- Do not copy `/Users/fzkuji/Downloads/sitemap (1).xml` into the repository.
- Keep `tools/docs_site/build.py` as the only sitemap generator.
- Publish the exact token `google-site-verification: google01b0015fda12129e.html`.
- Add no dependencies.

---

### Task 1: Publish and verify the Search Console proof

**Files:**
- Create: `docs/_static_root/google01b0015fda12129e.html`
- Modify: `.github/workflows/docs-pages.yml`

**Interfaces:**
- Consumes: files copied by `tools.docs_site.build._copy_static_root()` into `docs/_site/`
- Produces: `_publish/google01b0015fda12129e.html` with the exact Google verification token

- [ ] **Step 1: Verify the current assembly omits the proof**

Run the existing build and assembly commands in a temporary directory, then run:

```bash
test -f "$publish_dir/google01b0015fda12129e.html"
```

Expected: FAIL because no proof source or promotion rule exists.

- [ ] **Step 2: Add the proof source**

Create `docs/_static_root/google01b0015fda12129e.html` containing exactly:

```text
google-site-verification: google01b0015fda12129e.html
```

- [ ] **Step 3: Promote and check the proof in the workflow**

Extend the existing root-file loop with `google*.html`, then add:

```bash
grep -Fx 'google-site-verification: google01b0015fda12129e.html' \
  _publish/google01b0015fda12129e.html
```

- [ ] **Step 4: Verify the assembled site**

Run:

```bash
OPENPROGRAM_DOCS_BASE=/docs/ OPENPROGRAM_DOCS_ORIGIN=https://openprogram.io \
  python -m tools.docs_site.build
python -m tools.docs_site.checklinks
```

Repeat the workflow assembly locally and verify the proof, sitemap, robots file, and URL count.

- [ ] **Step 5: Publish and verify online**

Commit and push only the proof, workflow, design, and plan files. Wait for `Publish openprogram.io`, then fetch the proof URL and sitemap over HTTPS.
