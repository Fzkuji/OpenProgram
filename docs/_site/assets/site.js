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
        <div class="r-path">${r.doc.url}</div>
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
