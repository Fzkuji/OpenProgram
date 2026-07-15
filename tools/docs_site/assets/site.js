// OpenProgram Docs — runtime: SPA navigation, theme, i18n, search, toc, drawer.
//
// Navigation model: every page is a fully rendered static document, but clicks
// on internal links are intercepted — the target page is fetched (often already
// prefetched on hover), and only the parts that differ (article, toc, sidebar
// tree, tabbar active state) are swapped in. history.pushState keeps the URL
// honest, so deep links, reloads and back/forward all still work without JS.
(function () {
  "use strict";
  const ROOT = document.documentElement;
  const BASE = ROOT.dataset.base || "/docs/"; // absolute mount prefix

  // ── theme ──────────────────────────────────────────────────────────────
  const pygLight = document.getElementById("pyg-light");
  const pygDark = document.getElementById("pyg-dark");
  function syncPygments(theme) {
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

  // ── i18n (UI chrome; body language is per-document) ───────────────────
  const I18N = {
    zh: {
      search: "搜索文档", search_ph: "搜索标题或正文…", on_this_page: "本页内容",
      prev: "上一篇", next: "下一篇", updated: "最后更新", nav_filter: "过滤目录…",
      copy: "复制", copied: "已复制 ✓", copy_fail: "复制失败", search_empty: "无匹配结果",
    },
    en: {
      search: "Search docs", search_ph: "Search titles or text…", on_this_page: "On this page",
      prev: "Previous", next: "Next", updated: "Last updated", nav_filter: "Filter docs…",
      copy: "Copy", copied: "Copied ✓", copy_fail: "Copy failed", search_empty: "No results",
    },
  };
  let curLang = "en";
  try { curLang = localStorage.getItem("op-docs-lang") || "en"; } catch (e) {}
  const pageLang0 = ROOT.getAttribute("data-page-lang");
  if (pageLang0 === "en" || pageLang0 === "zh") curLang = pageLang0;
  function t(key) { return (I18N[curLang] || I18N.en)[key]; }

  function applyLang(lang) {
    const d = I18N[lang] || I18N.en;
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const v = d[el.getAttribute("data-i18n")];
      if (v != null) el.textContent = v;
    });
    document.querySelectorAll("[data-i18n-ph]").forEach((el) => {
      const v = d[el.getAttribute("data-i18n-ph")];
      if (v != null) el.setAttribute("placeholder", v);
    });
    ROOT.setAttribute("lang", lang === "zh" ? "zh" : "en");
    const btn = document.getElementById("lang-toggle");
    if (btn) btn.textContent = lang === "zh" ? "中" : "EN";
    // Bilingual-labelled elements (sidebar links, group headers, tabs,
    // breadcrumbs, callout heads) switch text; links switch href too.
    document.querySelectorAll("[data-title-zh]").forEach((el) => {
      if (el.dataset.titleEn == null) el.dataset.titleEn = el.textContent; // capture once
      el.textContent = lang === "zh" ? el.getAttribute("data-title-zh") : el.dataset.titleEn;
      const hrefZh = el.getAttribute("data-href-zh");
      const hrefEn = el.getAttribute("data-href-en");
      if (lang === "zh" && hrefZh) el.setAttribute("href", hrefZh);
      else if (lang !== "zh" && hrefEn) el.setAttribute("href", hrefEn);
    });
    document.querySelectorAll("article .copy-btn:not(.copied)").forEach((b) => { b.textContent = d.copy; });
  }

  const langBtn = document.getElementById("lang-toggle");
  if (langBtn) {
    langBtn.addEventListener("click", () => {
      curLang = curLang === "en" ? "zh" : "en";
      try { localStorage.setItem("op-docs-lang", curLang); } catch (e) {}
      applyLang(curLang);
      window.dispatchEvent(new CustomEvent("documentLangChange", { detail: { lang: curLang } }));
      const altUrl = ROOT.getAttribute("data-alt-lang-url");
      const pl = ROOT.getAttribute("data-page-lang");
      if (altUrl && pl && pl !== curLang) navigate(altUrl); // stay in-app
    });
  }

  // ── sidebar drawer / collapse ──────────────────────────────────────────
  const scrim = document.querySelector(".scrim");
  const hamburger = document.querySelector(".hamburger");
  function sidebarEl() { return document.querySelector("nav.sidebar"); }
  function closeDrawer() {
    const nav = sidebarEl();
    nav && nav.classList.remove("open");
    scrim && scrim.classList.remove("show");
  }
  try {
    if (localStorage.getItem("op-docs-nav-collapsed") === "1")
      ROOT.setAttribute("data-nav-collapsed", "1");
  } catch (e) {}
  if (hamburger) hamburger.addEventListener("click", () => {
    const nav = sidebarEl();
    if (window.innerWidth > 860) {
      const next = ROOT.getAttribute("data-nav-collapsed") === "1" ? "0" : "1";
      ROOT.setAttribute("data-nav-collapsed", next);
      try { localStorage.setItem("op-docs-nav-collapsed", next); } catch (e) {}
    } else if (nav) {
      nav.classList.toggle("open"); scrim && scrim.classList.toggle("show");
    }
  });
  if (scrim) scrim.addEventListener("click", closeDrawer);

  // ── per-page wiring (re-run after every SPA swap) ──────────────────────
  let tocObserver = null;

  function initSidebar() {
    const active = document.querySelector("nav.sidebar a.navlink.active");
    if (active) active.scrollIntoView({ block: "center" });

    const navFilter = document.querySelector(".nav-filter");
    if (navFilter && !navFilter.dataset.bound) {
      navFilter.dataset.bound = "1";
      navFilter.addEventListener("input", () => {
        const q = navFilter.value.trim().toLowerCase();
        document.querySelectorAll("nav.sidebar a.navlink").forEach((a) => {
          a.style.display = !q || a.textContent.toLowerCase().includes(q) ? "" : "none";
        });
        // a section is visible iff it still has a visible link
        document.querySelectorAll("nav.sidebar .nav-sec").forEach((sec) => {
          const hasMatch = !q || sec.querySelector('a.navlink:not([style*="display: none"])');
          sec.style.display = hasMatch ? "" : "none";
        });
      });
    }
  }

  function initArticle() {
    // code-block copy buttons
    document.querySelectorAll("article pre").forEach((pre) => {
      if (pre.querySelector(".copy-btn")) return;
      const btn = document.createElement("button");
      btn.className = "copy-btn";
      btn.type = "button";
      btn.textContent = t("copy");
      btn.addEventListener("click", () => {
        const code = pre.querySelector("code") || pre;
        navigator.clipboard.writeText(code.innerText).then(() => {
          btn.textContent = t("copied");
          btn.classList.add("copied");
          setTimeout(() => { btn.textContent = t("copy"); btn.classList.remove("copied"); }, 1500);
        }).catch(() => { btn.textContent = t("copy_fail"); });
      });
      pre.appendChild(btn);
    });

    // toc scroll-spy
    if (tocObserver) { tocObserver.disconnect(); tocObserver = null; }
    const tocLinks = Array.from(document.querySelectorAll("aside.toc a"));
    if (tocLinks.length) {
      const map = new Map();
      tocLinks.forEach((a) => {
        const id = decodeURIComponent(a.getAttribute("href").slice(1));
        const el = document.getElementById(id);
        if (el) map.set(el, a);
      });
      tocObserver = new IntersectionObserver((entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            tocLinks.forEach((l) => l.classList.remove("active"));
            const a = map.get(e.target);
            if (a) a.classList.add("active");
          }
        });
      }, { rootMargin: "-116px 0px -70% 0px", threshold: 0 });
      map.forEach((_a, el) => tocObserver.observe(el));
    }
  }

  function initPage() {
    initSidebar();
    initArticle();
    applyLang(curLang);
  }

  // ── SPA navigation ─────────────────────────────────────────────────────
  const pageCache = new Map(); // url -> Document
  const CACHE_MAX = 80;

  function cachePut(url, doc) {
    if (pageCache.size >= CACHE_MAX) {
      const first = pageCache.keys().next().value;
      pageCache.delete(first);
    }
    pageCache.set(url, doc);
  }

  function normalize(href) {
    const u = new URL(href, location.href);
    return u.pathname + u.hash;
  }

  function isInternalPage(href) {
    let u;
    try { u = new URL(href, location.href); } catch (e) { return false; }
    if (u.origin !== location.origin) return false;
    if (!u.pathname.startsWith(BASE)) return false;
    const p = u.pathname;
    if (p.endsWith(".raw.html")) return false; // standalone full pages
    return p.endsWith(".html") || p.endsWith("/");
  }

  function fetchPage(pathname) {
    const key = pathname;
    if (pageCache.has(key)) return Promise.resolve(pageCache.get(key));
    return fetch(pathname).then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.text();
    }).then((html) => {
      const doc = new DOMParser().parseFromString(html, "text/html");
      cachePut(key, doc);
      return doc;
    });
  }

  function swapFrom(doc, pathname) {
    // <html> metadata
    const newRoot = doc.documentElement;
    ["data-page-lang", "data-alt-lang-url"].forEach((attr) => {
      const v = newRoot.getAttribute(attr);
      if (v == null) ROOT.removeAttribute(attr); else ROOT.setAttribute(attr, v);
    });
    document.title = doc.title;

    // tabbar (active state)
    const curTabbar = document.querySelector("nav.tabbar");
    const newTabbar = doc.querySelector("nav.tabbar");
    if (curTabbar && newTabbar) curTabbar.innerHTML = newTabbar.innerHTML;

    // sidebar tree — swap only when it actually differs (tab change or
    // home/no-side page), so same-tab clicks keep scroll position; always
    // refresh the active link.
    const curLayout = document.querySelector(".layout");
    const newLayout = doc.querySelector(".layout");
    const curNav = document.querySelector("nav.sidebar .nav-tree");
    const newNav = doc.querySelector("nav.sidebar .nav-tree");
    if (curLayout && newLayout) curLayout.className = newLayout.className;
    const curSidebar = sidebarEl();
    const newSidebar = doc.querySelector("nav.sidebar");
    if (curSidebar && !newSidebar) {
      curSidebar.remove();
    } else if (!curSidebar && newSidebar && curLayout) {
      const clone = newSidebar.cloneNode(true);
      // cloned nodes carry data-bound flags but not the listeners — reset so
      // initSidebar rebinds them
      clone.querySelectorAll("[data-bound]").forEach((el) => el.removeAttribute("data-bound"));
      curLayout.insertAdjacentElement("afterbegin", clone);
    } else if (curNav && newNav) {
      const activeHref = (h) => {
        const a = document.querySelector('nav.sidebar a.navlink[href="' + h + '"]');
        return a != null;
      };
      const targetPath = pathname.split("#")[0];
      if (activeHref(targetPath)) {
        // same section list: just move the .active marker
        document.querySelectorAll("nav.sidebar a.navlink.active").forEach((a) => a.classList.remove("active"));
        const a = document.querySelector('nav.sidebar a.navlink[href="' + targetPath + '"]');
        if (a) {
          a.classList.add("active");
          a.scrollIntoView({ block: "nearest" });
        }
      } else {
        curNav.innerHTML = newNav.innerHTML;
      }
    }

    // main article + toc
    const curArticle = document.querySelector("main.content article");
    const newArticle = doc.querySelector("main.content article");
    if (curArticle && newArticle) curArticle.innerHTML = newArticle.innerHTML;
    const curToc = document.querySelector("aside.toc");
    const newToc = doc.querySelector("aside.toc");
    if (curToc) curToc.innerHTML = newToc ? newToc.innerHTML : "";
  }

  function afterSwap(pathname) {
    initPage();
    closeDrawer();
    const hash = pathname.includes("#") ? pathname.split("#")[1] : "";
    if (hash) {
      const el = document.getElementById(decodeURIComponent(hash));
      if (el) { el.scrollIntoView(); return; }
    }
    window.scrollTo(0, 0);
  }

  let navSeq = 0;
  function navigate(href, push) {
    if (push === undefined) push = true;
    const pathname = normalize(href);
    const clean = pathname.split("#")[0];
    const seq = ++navSeq;
    fetchPage(clean).then((doc) => {
      if (seq !== navSeq) return; // a newer navigation superseded this one
      const doSwap = () => { swapFrom(doc, pathname); afterSwap(pathname); };
      if (push) history.pushState({ spa: true }, "", pathname);
      if (document.startViewTransition) document.startViewTransition(doSwap);
      else doSwap();
    }).catch(() => { location.href = pathname; }); // graceful full-load fallback
  }
  window.opDocsNavigate = navigate;

  document.addEventListener("click", (e) => {
    if (e.defaultPrevented || e.button !== 0) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest ? e.target.closest("a[href]") : null;
    if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
    const href = a.getAttribute("href");
    if (!href || href.startsWith("#")) return; // in-page anchors scroll natively
    if (!isInternalPage(a.href)) return;
    e.preventDefault();
    const path = normalize(a.href);
    if (path.split("#")[0] === location.pathname && path.includes("#")) {
      // same page, different anchor
      const el = document.getElementById(decodeURIComponent(path.split("#")[1]));
      if (el) { history.pushState({ spa: true }, "", path); el.scrollIntoView(); }
      return;
    }
    navigate(a.href);
  });

  window.addEventListener("popstate", () => {
    navigate(location.pathname + location.hash, false);
  });

  // hover / touch prefetch: by the time the click lands, the page is cached
  let prefetchTimer = null;
  function maybePrefetch(e) {
    const a = e.target.closest ? e.target.closest("a[href]") : null;
    if (!a || !isInternalPage(a.href)) return;
    const clean = normalize(a.href).split("#")[0];
    if (pageCache.has(clean) || clean === location.pathname) return;
    clearTimeout(prefetchTimer);
    prefetchTimer = setTimeout(() => { fetchPage(clean).catch(() => {}); }, 65);
  }
  document.addEventListener("mouseover", maybePrefetch);
  document.addEventListener("touchstart", maybePrefetch, { passive: true });

  // ── search ─────────────────────────────────────────────────────────────
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
  function goResult(href) { closeSearch(); navigate(href); }

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
    if (!curResults.length) { resultsBox.innerHTML = '<div class="search-empty">' + t("search_empty") + "</div>"; return; }
    resultsBox.innerHTML = curResults.map((r, i) => {
      let snip = "";
      if (r.pos >= 0) {
        const start = Math.max(0, r.pos - 40);
        snip = (start > 0 ? "…" : "") + r.doc.text.slice(start, r.pos + 80) + "…";
        snip = highlight(snip.replace(/</g, "&lt;"), q);
      }
      return '<a href="' + BASE + r.doc.url + '" class="' + (i === 0 ? "sel" : "") + '" data-i="' + i + '">'
        + '<div class="r-title">' + highlight(r.doc.title.replace(/</g, "&lt;"), q) + "</div>"
        + '<div class="r-path">' + (r.doc.group ? r.doc.group.replace(/</g, "&lt;") : r.doc.url) + "</div>"
        + (snip ? '<div class="r-snippet">' + snip + "</div>" : "")
        + "</a>";
    }).join("");
  }

  if (input) {
    let timer;
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => runSearch(input.value), 90); });
    input.addEventListener("keydown", (e) => {
      const links = Array.from(resultsBox.querySelectorAll("a"));
      if (e.key === "ArrowDown") { e.preventDefault(); selIdx = Math.min(selIdx + 1, links.length - 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); selIdx = Math.max(selIdx - 1, 0); }
      else if (e.key === "Enter") { if (links[selIdx]) goResult(links[selIdx].getAttribute("href")); return; }
      else return;
      links.forEach((l, i) => l.classList.toggle("sel", i === selIdx));
      if (links[selIdx]) links[selIdx].scrollIntoView({ block: "nearest" });
    });
  }
  if (resultsBox) resultsBox.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (a) { e.preventDefault(); goResult(a.getAttribute("href")); }
  });

  // ── boot ───────────────────────────────────────────────────────────────
  history.replaceState({ spa: true }, "", location.pathname + location.hash);
  initPage();
})();
