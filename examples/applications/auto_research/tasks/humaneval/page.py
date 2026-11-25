"""The survey page: one self-contained HTML file (no network, no external script).

The page holds the guide and the tasks as JSON. Each rater's order is drawn in the
browser from a PRNG seeded by the rater's id: tasks shuffled, sides swapped at random
(pairwise), repeated tasks placed at least three tasks after their first showing with
the sides flipped, attention checks spread over the run. Raters see only codes, never
system names. Answers download as CSV (and show as text to copy) at the end.
"""

import html
import json

CSV_COLUMNS = ["survey_id", "rater", "position", "task", "kind", "repeat_of", "item", "criterion", "shown_1",
               "shown_2", "answer", "winner", "seconds", "finished_at"]

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f7f7f8;--card:#fff;--ink:#1d1d22;--muted:#62626e;--line:#dcdce3;--accent:#4b4fc4;--accent-ink:#fff;--soft:#eceefe}
@media (prefers-color-scheme: dark){:root{--bg:#15151a;--card:#1e1e25;--ink:#ececf1;--muted:#a0a0ad;--line:#34343f;--accent:#8a8dff;--accent-ink:#111;--soft:#262848}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.4rem;margin:0 0 12px}
h2{font-size:1.05rem;margin:0 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin:0 0 16px}
.muted{color:var(--muted);font-size:.92rem}
.text{white-space:pre-wrap;word-wrap:break-word}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:720px){.pair{grid-template-columns:1fr}}
.label{font-weight:600;margin-bottom:6px}
.q{border-top:1px solid var(--line);padding:12px 0 4px}
.q:first-child{border-top:0}
.opts{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}
.opts label{border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer;user-select:none}
.opts input{margin-right:6px}
.opts label:has(input:checked){background:var(--soft);border-color:var(--accent)}
button{background:var(--accent);color:var(--accent-ink);border:0;border-radius:8px;padding:10px 20px;font-size:1rem;cursor:pointer}
button:disabled{opacity:.45;cursor:default}
input[type=text]{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);width:min(320px,100%)}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin:0 0 16px}
.bar>div{height:100%;background:var(--accent)}
textarea{width:100%;height:160px;font:12px/1.4 ui-monospace,Menlo,monospace;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:8px}
.hidden{display:none}
</style>
</head>
<body>
<main>
<section id="start" class="card">
<h1>__TITLE__</h1>
<div id="instructions" class="text"></div>
<h2 style="margin-top:16px">What you will judge</h2>
<div id="criteria"></div>
<p class="muted" id="length"></p>
<p><label>Your participant ID<br><input type="text" id="rater" autocomplete="off"></label></p>
<p><label><input type="checkbox" id="consent"> I agree to take part. My answers are stored only with this ID.</label></p>
<button id="begin" disabled>Start</button>
</section>
<section id="task" class="hidden">
<div class="bar"><div id="progress"></div></div>
<div class="card"><div class="label">Input</div><div id="prompt" class="text"></div></div>
<div id="outputs"></div>
<div class="card" id="questions"></div>
<button id="next" disabled>Next</button>
</section>
<section id="done" class="card hidden">
<h1>Thank you</h1>
<p>Your completion code: <strong id="code"></strong></p>
<p><button id="download">Download your answers (CSV)</button></p>
<p class="muted">If the download does not start, copy the text below into a file named answers.csv.</p>
<textarea id="csv" readonly></textarea>
</section>
</main>
<script type="application/json" id="survey">__DATA__</script>
<script>
(function(){
"use strict";
var S = JSON.parse(document.getElementById("survey").textContent);
var COLS = __COLUMNS__;
function $(id){ return document.getElementById(id); }
function hash(s){ var h = 2166136261 >>> 0; for (var i = 0; i < s.length; i++){ h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; } return h; }
function prng(seed){ var a = seed >>> 0; return function(){ a = (a + 0x6D2B79F5) >>> 0; var t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
function shuffle(xs, rng){ for (var i = xs.length - 1; i > 0; i--){ var j = Math.floor(rng() * (i + 1)); var t = xs[i]; xs[i] = xs[j]; xs[j] = t; } return xs; }
function el(tag, cls, text){ var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

$("instructions").textContent = S.instructions;
S.criteria.forEach(function(c){ var d = el("div", "q"); d.appendChild(el("div", "label", c.name + ": " + c.question)); if (c.guidance) d.appendChild(el("div", "muted text", c.guidance)); $("criteria").appendChild(d); });
$("length").textContent = "About " + S.minutes + " minutes. Some pages check attention; some pages come back later.";
var params = new URLSearchParams(location.search);
$("rater").value = params.get("PROLIFIC_PID") || params.get("rater") || "";
function ready(){ $("begin").disabled = !($("rater").value.trim() && $("consent").checked); }
$("rater").addEventListener("input", ready); $("consent").addEventListener("change", ready); ready();

var seq = [], at = 0, rows = [], shownAt = 0, rater = "";
function plan(){
  var rng = prng(hash(S.survey_id + "|" + rater));
  var main = shuffle(S.tasks.slice(), rng);
  if (main.length > S.max_tasks) main = main.slice(0, S.max_tasks);
  seq = main.map(function(t){ return {task: t, kind: "main", flip: S.mode === "pairwise" && rng() < 0.5, repeat_of: ""}; });
  var room = seq.slice(0, Math.max(0, seq.length - 3));
  var picks = shuffle(room.slice(), rng).slice(0, Math.min(S.repeats, room.length));
  picks.forEach(function(p){
    var i = seq.indexOf(p), lo = i + 3, pos = lo + Math.floor(rng() * (seq.length - lo + 1));
    seq.splice(pos, 0, {task: p.task, kind: "repeat", flip: !p.flip, repeat_of: p.task.id});
  });
  var n = S.attention.length;
  S.attention.forEach(function(a, k){
    var pos = Math.floor((k + 0.5 + (rng() - 0.5) * 0.8) * seq.length / n);
    pos = Math.max(1, Math.min(seq.length, pos));
    seq.splice(pos, 0, {task: a, kind: "attention", flip: false, repeat_of: ""});
  });
}
function options(name, values, labels){
  var box = el("div", "opts");
  values.forEach(function(v, i){ var l = el("label"); var r = document.createElement("input"); r.type = "radio"; r.name = name; r.value = v; r.addEventListener("change", check); l.appendChild(r); l.appendChild(document.createTextNode(labels[i])); box.appendChild(l); });
  return box;
}
function check(){ $("next").disabled = S.criteria.some(function(c){ return !document.querySelector('input[name="c_' + c.id + '"]:checked'); }); }
function show(){
  var s = seq[at], t = s.task;
  $("progress").style.width = (100 * at / seq.length) + "%";
  $("prompt").textContent = t.prompt;
  var out = $("outputs"); out.innerHTML = ""; var q = $("questions"); q.innerHTML = "";
  if (S.mode === "pairwise"){
    var sides = s.flip ? [t.right, t.left] : [t.left, t.right];
    var grid = el("div", "pair");
    sides.forEach(function(o, i){ var c = el("div", "card"); c.appendChild(el("div", "label", "Response " + (i + 1))); c.appendChild(el("div", "text", o.text)); grid.appendChild(c); });
    out.appendChild(grid);
    s.shown = [sides[0].code, sides[1].code];
    S.criteria.forEach(function(c){ var d = el("div", "q"); d.appendChild(el("div", "label", c.question)); d.appendChild(options("c_" + c.id, ["1", "tie", "2"], ["Response 1 is better", "About the same", "Response 2 is better"])); q.appendChild(d); });
  } else {
    var c0 = el("div", "card"); c0.appendChild(el("div", "label", "Response")); c0.appendChild(el("div", "text", t.text)); out.appendChild(c0);
    s.shown = [t.code, ""];
    var vals = [], labs = [];
    for (var v = 1; v <= S.scale; v++){ vals.push(String(v)); labs.push(v === 1 ? "1 (worst)" : v === S.scale ? v + " (best)" : String(v)); }
    S.criteria.forEach(function(c){ var d = el("div", "q"); d.appendChild(el("div", "label", c.question)); d.appendChild(options("c_" + c.id, vals, labs)); q.appendChild(d); });
  }
  $("next").disabled = true; shownAt = Date.now(); window.scrollTo(0, 0);
}
function record(){
  var s = seq[at], secs = ((Date.now() - shownAt) / 1000).toFixed(1);
  S.criteria.forEach(function(c){
    var a = document.querySelector('input[name="c_' + c.id + '"]:checked').value, win = "";
    if (S.mode === "pairwise") win = a === "tie" ? "tie" : a === "1" ? s.shown[0] : s.shown[1];
    rows.push({survey_id: S.survey_id, rater: rater, position: at + 1, task: s.task.id, kind: s.kind, repeat_of: s.repeat_of, item: s.task.item, criterion: c.id, shown_1: s.shown[0], shown_2: s.shown[1], answer: a, winner: win, seconds: secs, finished_at: ""});
  });
}
function cell(v){ v = String(v); return /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }
function finish(){
  var stamp = new Date().toISOString();
  rows.forEach(function(r){ r.finished_at = stamp; });
  var text = COLS.join(",") + "\n" + rows.map(function(r){ return COLS.map(function(k){ return cell(r[k]); }).join(","); }).join("\n") + "\n";
  $("task").classList.add("hidden"); $("done").classList.remove("hidden");
  $("code").textContent = S.completion_code; $("csv").value = text;
  $("download").onclick = function(){
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], {type: "text/csv"}));
    a.download = "answers-" + S.survey_id + "-" + rater.replace(/[^A-Za-z0-9_-]/g, "_") + ".csv";
    document.body.appendChild(a); a.click(); a.remove();
  };
}
$("begin").addEventListener("click", function(){ rater = $("rater").value.trim(); plan(); $("start").classList.add("hidden"); $("task").classList.remove("hidden"); show(); });
$("next").addEventListener("click", function(){ record(); at += 1; if (at < seq.length) show(); else finish(); });
})();
</script>
</body>
</html>
"""


def render(data: dict) -> str:
    """The page for the survey data (title, mode, scale, instructions, criteria, tasks,
    attention, repeats, max_tasks, minutes, completion_code, survey_id)."""
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace(" ", "\\u2028") \
        .replace(" ", "\\u2029")
    return (TEMPLATE.replace("__TITLE__", html.escape(data["title"]))
            .replace("__COLUMNS__", json.dumps(CSV_COLUMNS))
            .replace("__DATA__", blob))


__all__ = ["CSV_COLUMNS", "render"]
