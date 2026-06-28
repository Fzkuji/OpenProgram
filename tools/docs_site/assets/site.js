// OpenProgram Docs — runtime: theme toggle, toc scroll-spy, search, mobile drawer.
(function () {
  "use strict";
  const ROOT = document.documentElement;
  const BASE = ROOT.dataset.base || "";

  // ── theme toggle (initial theme already set by inline head script) ──
  const pygLight = document.getElementById("pyg-light");
  const pygDark = document.getElementById("pyg-dark");
  function syncPygments(theme) {
    // Drive highlight stylesheets by explicit theme, not media query, so the
    // JS toggle works too.
    if (pygLight) { pygLight.media = "all"; pygLight.disabled = theme === "dark"; }
    if (pygDark) { pygDark.media = "all"; pygDark.disabled = theme !== "dark"; }
  }
  syncPygments(ROOT.getAttribute("data-theme"));

  const themeBtn = document.getElementById("theme-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const next = ROOT.getAttribute("data-theme") === "dark" ? "light" : "dark";
      ROOT.setAttribute("data-theme", next);
      try { localStorage.setItem("op-docs-theme", next); } catch (e) {}
      themeBtn.textContent = next === "dark" ? "☀" : "☾";
      syncPygments(next);
      window.dispatchEvent(new CustomEvent("documentThemeChange", { detail: { theme: next } }));
    });
    themeBtn.textContent = ROOT.getAttribute("data-theme") === "dark" ? "☀" : "☾";
  }

  // ── language toggle (UI chrome only; doc body stays in its source language) ──
  const I18N = {
    zh: {
      search: "搜索文档", search_ph: "搜索标题或正文…", on_this_page: "本页内容",
      prev: "上一篇", next: "下一篇", updated: "最后更新", nav_filter: "过滤目录…",
      home_title: "OpenProgram 设计文档",
      home_sub: "框架的设计笔记、API 与指南，按子系统组织。左侧目录浏览，或按 ",
      home_sub2: " 搜索。", unit: " 篇",
      grp_start: "快速上手", grp_integ: "集成", grp_ref: "参考",
      p_overview: "项目总览", p_overview_cn: "项目总览（中文）", p_start: "快速上手",
      p_install: "安装", p_features: "功能详解", p_int_cc: "集成 Claude Code",
      p_int_oc: "集成 OpenClaw", p_harness: "安装与编写 Harness", p_api: "API 参考",
      p_token: "Provider Token 追踪", p_trouble: "故障排查",
    },
    en: {
      search: "Search docs", search_ph: "Search titles or text…", on_this_page: "On this page",
      prev: "Previous", next: "Next", updated: "Last updated", nav_filter: "Filter docs…",
      home_title: "OpenProgram Documentation",
      home_sub: "Design notes, API and guides, organized by subsystem. Browse the sidebar, or press ",
      home_sub2: " to search.", unit: " docs",
      grp_start: "Getting Started", grp_integ: "Integrations", grp_ref: "Reference",
      p_overview: "Overview", p_overview_cn: "Overview (中文)", p_start: "Getting Started",
      p_install: "Install", p_features: "Features", p_int_cc: "Claude Code Integration",
      p_int_oc: "OpenClaw Integration", p_harness: "Install & Write Harnesses", p_api: "API Reference",
      p_token: "Provider Token Tracking", p_trouble: "Troubleshooting",
    },
  };
  function applyLang(lang) {
    const t = I18N[lang] || I18N.zh;
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const v = t[el.getAttribute("data-i18n")];
      if (v != null) el.textContent = v;
    });
    document.querySelectorAll("[data-i18n-ph]").forEach((el) => {
      const v = t[el.getAttribute("data-i18n-ph")];
      if (v != null) el.setAttribute("placeholder", v);
    });
    ROOT.setAttribute("lang", lang === "en" ? "en" : "zh");
    const btn = document.getElementById("lang-toggle");
    if (btn) btn.textContent = lang === "en" ? "EN" : "中";
  }
  let curLang = "zh";
  try { curLang = localStorage.getItem("op-docs-lang") || "zh"; } catch (e) {}
  applyLang(curLang);
  const langBtn = document.getElementById("lang-toggle");
  if (langBtn) {
    langBtn.addEventListener("click", () => {
      curLang = curLang === "en" ? "zh" : "en";
      try { localStorage.setItem("op-docs-lang", curLang); } catch (e) {}
      applyLang(curLang);
      window.dispatchEvent(new CustomEvent("documentLangChange", { detail: { lang: curLang } }));
    });
  }

  // ── mobile drawer ──
  const nav = document.querySelector("nav.sidebar");
  const scrim = document.querySelector(".scrim");
  const hamburger = document.querySelector(".hamburger");
  function closeDrawer() { nav && nav.classList.remove("open"); scrim && scrim.classList.remove("show"); }
  if (hamburger) hamburger.addEventListener("click", () => {
    nav.classList.toggle("open"); scrim.classList.toggle("show");
  });
  if (scrim) scrim.addEventListener("click", closeDrawer);

  // ── sidebar collapse state memory ──
  (function () {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem("op-docs-nav") || "{}"); } catch (e) {}
    const groups = document.querySelectorAll("nav.sidebar details.group");
    groups.forEach((d) => {
      const key = d.getAttribute("data-key");
      // current-page branch stays open (server set [open]); otherwise honor saved.
      if (!d.hasAttribute("open") && saved[key] === true) d.setAttribute("open", "");
      d.addEventListener("toggle", () => {
        try {
          saved[key] = d.open;
          localStorage.setItem("op-docs-nav", JSON.stringify(saved));
        } catch (e) {}
      });
    });
    // scroll the active link into view within the sidebar
    const active = document.querySelector("nav.sidebar a.navlink.active");
    if (active) active.scrollIntoView({ block: "center" });
  })();

  // ── code-block copy buttons ──
  document.querySelectorAll("article pre").forEach((pre) => {
    if (pre.querySelector(".copy-btn")) return;
    const btn = document.createElement("button");
    btn.className = "copy-btn";
    btn.type = "button";
    btn.textContent = "复制";
    btn.addEventListener("click", () => {
      const code = pre.querySelector("code") || pre;
      navigator.clipboard.writeText(code.innerText).then(() => {
        btn.textContent = "已复制 ✓";
        btn.classList.add("copied");
        setTimeout(() => { btn.textContent = "复制"; btn.classList.remove("copied"); }, 1500);
      }).catch(() => { btn.textContent = "复制失败"; });
    });
    pre.appendChild(btn);
  });

  // ── sidebar filter ──
  const navFilter = document.querySelector(".nav-filter");
  if (navFilter) {
    const allLinks = Array.from(document.querySelectorAll("nav.sidebar a.navlink"));
    const allGroups = Array.from(document.querySelectorAll("nav.sidebar details.group"));
    const savedOpen = new WeakMap();
    navFilter.addEventListener("input", () => {
      const q = navFilter.value.trim().toLowerCase();
      if (!q) {
        allLinks.forEach((a) => (a.style.display = ""));
        allGroups.forEach((g) => {
          g.style.display = "";
          if (savedOpen.has(g)) g.open = savedOpen.get(g);
        });
        return;
      }
      // remember original open-state once, on first filter keystroke
      allGroups.forEach((g) => { if (!savedOpen.has(g)) savedOpen.set(g, g.open); });
      allLinks.forEach((a) => {
        a.style.display = a.textContent.toLowerCase().includes(q) ? "" : "none";
      });
      // a group is visible iff it has any visible link; expand visible ones
      allGroups.forEach((g) => {
        const hasMatch = g.querySelector('a.navlink:not([style*="display: none"])');
        g.style.display = hasMatch ? "" : "none";
        if (hasMatch) g.open = true;
      });
    });
  }

  // ── toc scroll-spy ──
  const tocLinks = Array.from(document.querySelectorAll("aside.toc a"));
  if (tocLinks.length) {
    const map = new Map();
    tocLinks.forEach((a) => {
      const id = decodeURIComponent(a.getAttribute("href").slice(1));
      const el = document.getElementById(id);
      if (el) map.set(el, a);
    });
    const obs = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          tocLinks.forEach((l) => l.classList.remove("active"));
          const a = map.get(e.target);
          if (a) a.classList.add("active");
        }
      });
    }, { rootMargin: "-76px 0px -70% 0px", threshold: 0 });
    map.forEach((_a, el) => obs.observe(el));
  }

  // ── search ──
  const overlay = document.querySelector(".search-overlay");
  const input = overlay && overlay.querySelector("input");
  const resultsBox = overlay && overlay.querySelector(".search-results");
  let index = null, selIdx = -1, curResults = [];

  function openSearch() {
    if (!overlay) return;
    overlay.classList.add("open");
    input.value = ""; resultsBox.innerHTML = ""; selIdx = -1; curResults = [];
    input.focus();
    if (!index) {
      fetch(BASE + "search-index.json").then((r) => r.json()).then((d) => { index = d; });
    }
  }
  function closeSearch() { overlay && overlay.classList.remove("open"); }

  document.querySelectorAll(".search-trigger").forEach((b) => b.addEventListener("click", openSearch));
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openSearch(); }
    if (e.key === "Escape") closeSearch();
  });
  if (overlay) overlay.addEventListener("click", (e) => { if (e.target === overlay) closeSearch(); });

  function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
  function highlight(text, q) {
    const re = new RegExp("(" + escapeRe(q) + ")", "ig");
    return text.replace(re, "<mark>$1</mark>");
  }

  function runSearch(q) {
    if (!index || !q.trim()) { resultsBox.innerHTML = ""; curResults = []; return; }
    const ql = q.toLowerCase();
    const scored = [];
    for (const doc of index) {
      const tl = doc.title.toLowerCase();
      const bl = doc.text.toLowerCase();
      let score = 0, pos = -1;
      if (tl.includes(ql)) score += 10;
      pos = bl.indexOf(ql);
      if (pos >= 0) score += 3;
      if (score > 0) scored.push({ doc, score, pos });
    }
    scored.sort((a, b) => b.score - a.score);
    curResults = scored.slice(0, 30);
    selIdx = curResults.length ? 0 : -1;
    if (!curResults.length) { resultsBox.innerHTML = '<div class="search-empty">无匹配结果</div>'; return; }
    resultsBox.innerHTML = curResults.map((r, i) => {
      let snip = "";
      if (r.pos >= 0) {
        const start = Math.max(0, r.pos - 40);
        snip = (start > 0 ? "…" : "") + r.doc.text.slice(start, r.pos + 80) + "…";
        snip = highlight(snip.replace(/</g, "&lt;"), q);
      }
      return `<a href="${BASE + r.doc.url}" class="${i === 0 ? "sel" : ""}" data-i="${i}">
        <div class="r-title">${highlight(r.doc.title.replace(/</g, "&lt;"), q)}</div>
        <div class="r-path">${r.doc.group ? r.doc.group.replace(/</g, "&lt;") : r.doc.url}</div>
        ${snip ? `<div class="r-snippet">${snip}</div>` : ""}
      </a>`;
    }).join("");
  }

  if (input) {
    let t;
    input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => runSearch(input.value), 90); });
    input.addEventListener("keydown", (e) => {
      const links = Array.from(resultsBox.querySelectorAll("a"));
      if (e.key === "ArrowDown") { e.preventDefault(); selIdx = Math.min(selIdx + 1, links.length - 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); selIdx = Math.max(selIdx - 1, 0); }
      else if (e.key === "Enter") { if (links[selIdx]) location.href = links[selIdx].href; return; }
      else return;
      links.forEach((l, i) => l.classList.toggle("sel", i === selIdx));
      if (links[selIdx]) links[selIdx].scrollIntoView({ block: "nearest" });
    });
  }
})();
