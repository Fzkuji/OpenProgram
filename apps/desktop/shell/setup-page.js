"use strict";
const theme = new URLSearchParams(location.search).get("theme");
if (theme === "dark" || theme === "light") document.documentElement.dataset.theme = theme;

const bar = document.getElementById("bar");
const fill = document.getElementById("fill");
const percent = document.getElementById("percent");
const retryRow = document.getElementById("retry-row");
const title = document.getElementById("title");
const copy = document.getElementById("copy");
const ORIGINAL = { title: title.textContent, copy: copy.textContent };

function show({ percent: value, error }) {
  const clamped = Math.max(0, Math.min(100, Number(value) || 0));
  bar.setAttribute("aria-valuenow", String(clamped));
  fill.style.width = `${clamped}%`;
  percent.textContent = `${clamped}%`;
  bar.hidden = Boolean(error);
  percent.hidden = Boolean(error);
  retryRow.hidden = !error;
  title.textContent = error ? "OpenProgram couldn't finish setting up" : ORIGINAL.title;
  copy.textContent = error || ORIGINAL.copy;
}

document.getElementById("retry").addEventListener("click", () => window.openprogramShell.retrySetup());
window.openprogramShell.onSetup(show);
