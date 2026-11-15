import { evaluate, format, plain, openParens, CalcError } from "./calc.js";

const $ = id => document.getElementById(id);
const input = $("expression");
const resultEl = $("result");
const hint = $("hint");
const ghost = $("ghost");
const api = window.openprogramApp;
const HISTORY_LIMIT = 200;

const state = {
  degrees: true,
  ans: 0,
  lastResult: null,   // number shown after Enter
  justEvaluated: false,
  history: [],        // newest first: {expr, result, at}
  version: null,
  recall: -1,
};

// ---------- persistence (storage.app) ----------
async function loadSaved() {
  if (!api) return;
  try {
    const saved = await api.load();
    state.version = saved.version;
    const value = saved.value || {};
    state.history = Array.isArray(value.history) ? value.history.slice(0, HISTORY_LIMIT) : [];
    if (value.angle === "rad") setAngle(false, false);
    if (typeof value.ans === "number") state.ans = value.ans;
    renderHistory();
  } catch (error) {
    toast("History unavailable: " + error.message);
  }
}

let saving = Promise.resolve();
function persist() {
  if (!api || state.version === null) return;
  saving = saving.then(async () => {
    const value = { history: state.history, angle: state.degrees ? "deg" : "rad", ans: state.ans };
    try {
      const saved = await api.save(value, state.version);
      state.version = saved.version;
    } catch (error) {
      // Another view saved first: merge its history with ours and retry once.
      try {
        const latest = await api.load();
        const theirs = (latest.value && latest.value.history) || [];
        const seen = new Set(state.history.map(h => h.at + h.expr));
        state.history = [...state.history, ...theirs.filter(h => !seen.has(h.at + h.expr))]
          .sort((a, b) => b.at - a.at).slice(0, HISTORY_LIMIT);
        const saved = await api.save({ ...value, history: state.history }, latest.version);
        state.version = saved.version;
        renderHistory();
      } catch (retry) {
        toast("Could not save history: " + retry.message);
      }
    }
  });
}

// ---------- evaluation ----------
function compute(text) {
  const closed = text + ")".repeat(openParens(text));
  return evaluate(closed, { degrees: state.degrees, ans: state.ans });
}

function refresh() {
  const text = input.value;
  const missing = openParens(text);
  const suffix = state.justEvaluated ? " =" : ")".repeat(missing);
  if (suffix) {
    // Shows the implied closing parentheses (or "=" after a result) after the text.
    const measure = document.createElement("span");
    measure.style.cssText = "position:absolute;visibility:hidden;white-space:pre;font:400 22px/1.3 var(--mono)";
    measure.textContent = suffix;
    document.body.append(measure);
    input.style.paddingRight = measure.getBoundingClientRect().width + "px";
    measure.remove();
  } else input.style.paddingRight = "0";
  ghost.textContent = suffix;

  resultEl.classList.remove("error");
  hint.classList.remove("error");
  if (state.justEvaluated) return;
  if (!text.trim()) {
    resultEl.textContent = "0";
    resultEl.classList.add("preview");
    setHint();
    return;
  }
  try {
    const value = compute(text);
    resultEl.textContent = format(value);
    resultEl.classList.add("preview");
    setHint();
  } catch (error) {
    // Incomplete input while typing is normal; keep the last preview quiet.
    resultEl.classList.add("preview");
    if (error instanceof CalcError) setHint(error.message, true);
  }
}

function setHint(message, isError = false) {
  if (message) { hint.textContent = message; hint.classList.toggle("error", isError); return; }
  hint.innerHTML = 'Type an expression · <span class="kbd">Enter</span> to calculate';
}

function equals() {
  const text = input.value.trim();
  if (!text) return;
  try {
    const value = compute(text);
    const shown = text + ")".repeat(openParens(text));
    state.ans = value;
    state.lastResult = value;
    state.justEvaluated = true;
    resultEl.classList.remove("preview", "error");
    resultEl.textContent = format(value);
    input.value = shown;
    setHint();
    refresh();
    state.history.unshift({ expr: shown, result: value, at: Date.now() });
    state.history = state.history.slice(0, HISTORY_LIMIT);
    state.recall = -1;
    renderHistory();
    persist();
  } catch (error) {
    resultEl.classList.remove("preview");
    resultEl.classList.add("error");
    resultEl.textContent = error instanceof CalcError ? error.message : "Error";
    setHint();
  }
}

// After "=", typing an operator continues from the result; typing a
// number starts a new expression.
function beginEdit(insertText) {
  if (!state.justEvaluated) return;
  state.justEvaluated = false;
  const continues = /^[+\-−×÷*/^²!%)]/.test(insertText);
  input.value = continues ? plain(state.lastResult) : "";
  input.setSelectionRange(input.value.length, input.value.length);
}

function insert(text) {
  beginEdit(text);
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? input.value.length;
  input.setRangeText(text, start, end, "end");
  input.focus();
  refresh();
}

function backspace() {
  if (state.justEvaluated) { state.justEvaluated = false; refresh(); return; }
  const start = input.selectionStart, end = input.selectionEnd;
  if (start !== end) input.setRangeText("", start, end, "end");
  else if (start > 0) {
    // Remove a whole function name such as "sin(" at once.
    const before = input.value.slice(0, start);
    const m = /(asin|acos|atan|sin|cos|tan|ln|log|sqrt|abs|exp|√)\($|Ans$/.exec(before);
    const n = m ? m[0].length : 1;
    input.setRangeText("", start - n, start, "end");
  }
  input.focus();
  refresh();
}

function clearAll() {
  input.value = "";
  state.justEvaluated = false;
  state.recall = -1;
  input.focus();
  refresh();
}

function negate() {
  beginEdit("−");
  const text = input.value;
  // Toggle the sign of the trailing number, or of the whole expression.
  const m = /(^|[+\-−×÷*/^(])(\d*\.?\d+(?:E-?\d+)?)$/.exec(text);
  if (m) {
    const at = text.length - m[2].length;
    if (text[at - 1] === "−" && (at - 1 === 0 || "(×÷*/^".includes(text[at - 2]))) input.value = text.slice(0, at - 1) + m[2];
    else input.value = text.slice(0, at) + "−" + m[2];
  } else if (text) input.value = text.startsWith("−(") && text.endsWith(")") ? text.slice(2, -1) : `−(${text})`;
  else input.value = "−";
  input.focus();
  refresh();
}

function reciprocal() {
  if (state.justEvaluated) { state.justEvaluated = false; input.value = `1÷(${plain(state.lastResult)})`; }
  else input.value = input.value ? `1÷(${input.value})` : "1÷";
  input.focus();
  refresh();
}

async function copyResult() {
  let value;
  if (state.justEvaluated && state.lastResult !== null) value = state.lastResult;
  else { try { value = compute(input.value); } catch { value = null; } }
  if (value === null || value === undefined) { toast("Nothing to copy"); return; }
  const text = plain(value);
  const ok = await writeClipboard(text);
  toast(ok ? `Copied ${text}` : "Copy is blocked here — select the result and copy");
}

async function writeClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.cssText = "position:fixed;opacity:0";
    document.body.append(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    area.remove();
    input.focus();
    return ok;
  }
}

function setAngle(degrees, save = true) {
  state.degrees = degrees;
  document.querySelectorAll("[data-angle]").forEach(b => b.setAttribute("aria-checked", String((b.dataset.angle === "deg") === degrees)));
  if (save) persist();
  if (!state.justEvaluated) refresh();
}

// ---------- history ----------
function dayLabel(at) {
  const d = new Date(at), today = new Date();
  const start = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = (start(today) - start(d)) / 86400000;
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString("en", { month: "short", day: "numeric", year: d.getFullYear() === today.getFullYear() ? undefined : "numeric" });
}

function renderHistory() {
  const list = $("history-list");
  list.replaceChildren();
  let lastDay = null;
  state.history.forEach((item, index) => {
    const day = dayLabel(item.at);
    if (day !== lastDay) {
      const label = document.createElement("li");
      label.className = "h-day";
      label.textContent = day;
      list.append(label);
      lastDay = day;
    }
    const li = document.createElement("li");
    if (index === state.recall) li.classList.add("active");
    const button = document.createElement("button");
    button.type = "button";
    button.innerHTML = `<span class="h-expr"></span><span class="h-result"></span>`;
    button.querySelector(".h-expr").textContent = item.expr + " =";
    button.querySelector(".h-result").textContent = format(item.result);
    button.addEventListener("click", event => {
      // Click recalls the expression; Alt-click inserts the result.
      if (event.altKey) insert(plain(item.result));
      else recallAt(index);
    });
    li.append(button);
    list.append(li);
  });
  const empty = state.history.length === 0;
  $("history-empty").hidden = !empty;
  list.hidden = empty;
  $("clear-history").disabled = empty;
}

function recallAt(index) {
  const item = state.history[index];
  if (!item) return;
  state.recall = index;
  state.justEvaluated = false;
  input.value = item.expr;
  input.setSelectionRange(input.value.length, input.value.length);
  input.focus();
  renderHistory();
  refresh();
}

// ---------- events ----------
const KEY_FOR = { "*": "×", "/": "÷", "-": "−" };

input.addEventListener("beforeinput", event => {
  if (event.inputType !== "insertText" || !event.data) return;
  if (state.justEvaluated) {
    event.preventDefault();
    insert(KEY_FOR[event.data] || event.data);
    return;
  }
  if (KEY_FOR[event.data]) {
    event.preventDefault();
    insert(KEY_FOR[event.data]);
  }
});
input.addEventListener("input", () => { state.recall = -1; refresh(); });

document.addEventListener("keydown", event => {
  const mod = event.metaKey || event.ctrlKey;
  if (event.key === "Enter" || (event.key === "=" && !mod)) { event.preventDefault(); flash("equals"); equals(); return; }
  if (event.key === "Escape") { event.preventDefault(); flash("clear"); clearAll(); return; }
  if (mod && event.key.toLowerCase() === "c" && input.selectionStart === input.selectionEnd) { event.preventDefault(); copyResult(); return; }
  if (event.key === "ArrowUp") { event.preventDefault(); recallAt(Math.min(state.recall + 1, state.history.length - 1)); return; }
  if (event.key === "ArrowDown") {
    event.preventDefault();
    if (state.recall > 0) recallAt(state.recall - 1); else clearAll();
    return;
  }
  if (event.key === "Backspace" && state.justEvaluated) { event.preventDefault(); backspace(); return; }
  if (document.activeElement !== input && !mod && event.key.length === 1) {
    // Typing anywhere goes to the expression.
    input.focus();
  }
});

function flash(action) {
  const key = document.querySelector(`[data-action="${action}"]`);
  if (!key) return;
  key.classList.add("pressed");
  setTimeout(() => key.classList.remove("pressed"), 110);
}

$("keypad").addEventListener("mousedown", event => {
  // Keep the caret in the expression while clicking keys.
  if (event.target.closest("button")) event.preventDefault();
});
$("keypad").addEventListener("click", event => {
  const key = event.target.closest("button");
  if (!key) return;
  if (key.dataset.insert) insert(key.dataset.insert);
  else ({ clear: clearAll, backspace, equals, negate, reciprocal })[key.dataset.action]?.();
});
document.querySelectorAll("[data-angle]").forEach(b => b.addEventListener("click", () => setAngle(b.dataset.angle === "deg")));
$("copy").addEventListener("click", copyResult);
$("clear-history").addEventListener("click", () => {
  state.history = [];
  state.recall = -1;
  renderHistory();
  persist();
});

let toastTimer;
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 1800);
}

renderHistory();
refresh();
input.focus();
if (api?.ready) api.ready.then(loadSaved); else loadSaved();
