// Shared docs theme: runs synchronously in the head, then binds the header menu.
(function () {
  "use strict";
  const root = document.documentElement;
  const key = "op-docs-theme";
  const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const normalize = (value) => value === "light" || value === "dark" ? value : "system";
  let storage = null;
  let preference = "system";
  try {
    storage = window.localStorage;
    preference = normalize(storage.getItem(key));
  } catch (e) { /* System preference still works when storage is unavailable. */ }

  function syncControls() {
    const zh = root.getAttribute("lang") === "zh";
    const labels = zh
      ? { system: "跟随系统", light: "浅色", dark: "深色" }
      : { system: "Follow system", light: "Light", dark: "Dark" };
    const button = document.getElementById("theme-toggle");
    if (button) {
      const label = (zh ? "主题：" : "Theme: ") + labels[preference];
      button.setAttribute("aria-label", label);
      button.title = label;
    }
    document.querySelectorAll(".theme-opt").forEach((option) => {
      const mode = option.getAttribute("data-theme-choice");
      const active = mode === preference;
      option.classList.toggle("active", active);
      option.setAttribute("aria-checked", String(active));
      option.querySelector("span").textContent = labels[mode];
    });
  }

  function applyTheme() {
    const theme = preference === "system" ? (media && media.matches ? "dark" : "light") : preference;
    const changed = root.getAttribute("data-theme") !== theme;
    root.setAttribute("data-theme-mode", preference);
    root.setAttribute("data-theme", theme);
    root.style.colorScheme = theme;
    const light = document.getElementById("pyg-light");
    const dark = document.getElementById("pyg-dark");
    if (light) light.media = theme === "dark" ? "not all" : "all";
    if (dark) dark.media = theme === "dark" ? "all" : "not all";
    syncControls();
    if (changed) window.dispatchEvent(new CustomEvent("documentThemeChange", { detail: { theme } }));
  }

  function selectPreference(value, persist) {
    preference = normalize(value);
    if (persist) {
      try { if (storage) storage.setItem(key, preference); } catch (e) {}
    }
    applyTheme();
  }

  applyTheme();
  if (media) {
    const onSystemChange = () => { if (preference === "system") applyTheme(); };
    if (media.addEventListener) media.addEventListener("change", onSystemChange);
    else if (media.addListener) media.addListener(onSystemChange);
  }
  window.addEventListener("storage", (event) => {
    if (event.storageArea && event.storageArea !== storage) return;
    if (event.key === key || event.key === null) selectPreference(event.newValue, false);
  });
  window.addEventListener("documentLangChange", syncControls);

  function bindMenu() {
    applyTheme();
    const button = document.getElementById("theme-toggle");
    const wrap = document.querySelector(".theme-wrap");
    if (!button || !wrap) return;
    const menu = document.getElementById("theme-menu");
    const options = [...wrap.querySelectorAll(".theme-opt")];
    function close(focus = false) {
      wrap.classList.remove("open");
      button.setAttribute("aria-expanded", "false");
      if (focus) button.focus();
    }
    function open() {
      wrap.classList.add("open");
      button.setAttribute("aria-expanded", "true");
      (options.find((option) => option.getAttribute("aria-checked") === "true") || options[0]).focus();
    }
    button.addEventListener("click", () => {
      if (wrap.classList.contains("open")) close(); else open();
    });
    button.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        open();
      }
    });
    options.forEach((option) => option.addEventListener("click", () => {
      selectPreference(option.getAttribute("data-theme-choice"), true);
      close(true);
    }));
    menu.addEventListener("keydown", (event) => {
      const index = options.indexOf(document.activeElement);
      let next;
      if (event.key === "ArrowDown") next = (index + 1) % options.length;
      else if (event.key === "ArrowUp") next = (index + options.length - 1) % options.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = options.length - 1;
      if (next !== undefined) {
        event.preventDefault();
        options[next].focus();
      }
    });
    document.addEventListener("click", (event) => { if (!wrap.contains(event.target)) close(); });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && wrap.classList.contains("open")) close(true);
    });
    wrap.addEventListener("focusout", (event) => {
      if (!wrap.contains(event.relatedTarget)) close();
    });
    const languageButton = document.getElementById("lang-toggle");
    if (languageButton) languageButton.addEventListener("click", () => close());
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bindMenu, { once: true });
  else bindMenu();
})();
