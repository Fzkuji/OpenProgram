"""provenance.html: the paper's text with each word tinted by its class, the shares
overall and per section, and a replay of any paragraph clicked. One file, inline
CSS and JS, no external libraries."""

import html
import json

STYLE = """
:root{--bg:#fbfaf7;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e0d8;--card:#fff;
--human:transparent;--ai:#e7dcff;--ai_edit:#d7ebff;--pasted:#ffd9d2;--bulk:#fff0c2;--uncommitted:#e8e8e8;
--ai-s:#7a4fe0;--ai_edit-s:#2f7fd1;--pasted-s:#d2462a;--bulk-s:#c99400;--uncommitted-s:#8a8a8a;--human-s:#b9b5aa;
--ins:#c9f0d2;--del:#ffd6d6}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#17171a;--fg:#ececef;--muted:#9a9aa2;
--line:#2e2e34;--card:#1f1f24;--ai:#3a2c63;--ai_edit:#1d3954;--pasted:#5a2a22;--bulk:#4d3f12;--uncommitted:#38383d;
--ins:#1f4a2b;--del:#5a2626}}
:root[data-theme="dark"]{--bg:#17171a;--fg:#ececef;--muted:#9a9aa2;--line:#2e2e34;--card:#1f1f24;--ai:#3a2c63;
--ai_edit:#1d3954;--pasted:#5a2a22;--bulk:#4d3f12;--uncommitted:#38383d;--ins:#1f4a2b;--del:#5a2626}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{padding:20px 16px 8px;max-width:1200px;margin:auto}h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);font-size:13px}
main{display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:20px;max-width:1200px;margin:auto;padding:0 16px 40px}
@media (max-width:860px){main{grid-template-columns:1fr}aside{position:static!important;max-height:none!important}}
.bar{display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--line);margin:6px 0}
.bar span{display:block;height:100%}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13px;margin:8px 0}
.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:5px;vertical-align:-1px}
table{border-collapse:collapse;font-size:13px;width:100%;margin:8px 0 16px}
td,th{border-bottom:1px solid var(--line);padding:4px 6px;text-align:right}td:first-child,th:first-child{text-align:left}
h2{font-size:17px;margin:22px 0 6px;padding-top:6px;border-top:1px solid var(--line)}
.p{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--human-s);border-radius:8px;
padding:10px 12px;margin:10px 0;cursor:pointer}
.p:hover{border-color:var(--muted)}.p.on{outline:2px solid var(--ai_edit-s)}
.p .meta{font-size:12px;color:var(--muted);margin-top:6px;display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}
.c-ai{background:var(--ai)}.c-ai_edit{background:var(--ai_edit)}.c-pasted{background:var(--pasted)}
.c-bulk{background:var(--bulk)}.c-uncommitted{background:var(--uncommitted)}
aside{position:sticky;top:12px;max-height:calc(100vh - 24px);overflow:auto;background:var(--card);
border:1px solid var(--line);border-radius:8px;padding:12px 14px;align-self:start}
aside h3{margin:0 0 6px;font-size:15px}.nav{display:flex;gap:8px;align-items:center;margin:8px 0}
button{font:inherit;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:6px;
padding:3px 10px;cursor:pointer}button:disabled{opacity:.4;cursor:default}
input[type=range]{flex:1}.ver{font-size:13px;color:var(--muted);margin-bottom:8px}
.ins{background:var(--ins)}.del{background:var(--del);text-decoration:line-through}
.tag{display:inline-block;font-size:11px;border-radius:4px;padding:0 6px;border:1px solid var(--line)}
"""

SCRIPT = """
const R=JSON.parse(document.getElementById('data').textContent);
const C=['human','ai','ai_edit','pasted','bulk','uncommitted'];
const esc=s=>s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const bar=sh=>'<div class="bar">'+C.filter(c=>sh[c]>0).map(c=>
  `<span style="width:${sh[c]}%;background:var(--${c==='human'?'human-s':c+'-s'})" title="${R.classes[c]} ${sh[c]}%"></span>`).join('')+'</div>';
let cur=null,idx=0;
function show(id){
  const p=R.paragraphs.find(x=>x.id===id);cur=p;idx=p.replay.versions.length-1;
  document.querySelectorAll('.p').forEach(e=>e.classList.toggle('on',e.dataset.id===id));draw();}
function draw(){
  const a=document.getElementById('replay');if(!cur){return}
  const vs=cur.replay.versions,v=vs[idx];
  const when=v.date?v.date.slice(0,16).replace('T',' '):'工作区';
  a.innerHTML=`<h3>${cur.id} · ${esc(cur.section)}</h3><div class="ver">${esc(cur.file)}:${cur.start}-${cur.end}</div>`+
   bar(cur.shares)+`<div class="nav"><button id="pv" ${idx?'':'disabled'}>‹</button>`+
   `<input type="range" min="0" max="${vs.length-1}" value="${idx}" id="sl"><button id="nx" ${idx<vs.length-1?'':'disabled'}>›</button>`+
   `<span>${idx+1}/${vs.length}</span></div>`+
   `<div class="ver"><span class="tag c-${v.class}">${R.classes[v.class]||v.class}</span> ${esc(v.commit||'')} · ${esc(when)} · ${esc(v.author||'')}<br>${esc(v.subject||'')}`+
   (v.tools.length?'<br>AI：'+esc(v.tools.join('、')):'')+
   (cur.replay.omitted?`<br>中间省略 ${cur.replay.omitted} 个版本`:'')+'</div>'+
   '<div>'+v.diff.map(([op,t])=>op==='eq'?esc(t):`<span class="${op}">${esc(t)}</span>`).join(' ')+'</div>';
  document.getElementById('pv').onclick=()=>{idx--;draw()};
  document.getElementById('nx').onclick=()=>{idx++;draw()};
  document.getElementById('sl').oninput=e=>{idx=+e.target.value;draw()};}
document.getElementById('overall').innerHTML=bar(R.shares);
document.getElementById('legend').innerHTML=C.filter(c=>R.counts[c]).map(c=>
  `<span><i style="background:var(--${c==='human'?'human-s':c+'-s'})"></i>${R.classes[c]} ${R.shares[c]}%</span>`).join('');
let html='',sec=null;
for(const p of R.paragraphs){
  if(p.section!==sec){sec=p.section;html+=`<h2>${esc(sec)}</h2>`}
  const s=p.dominant==='human'?'human-s':p.dominant+'-s';
  html+=`<div class="p" data-id="${p.id}" style="border-left-color:var(--${s})">`+
   p.segments.map(([c,t])=>c==='human'?esc(t):`<span class="c-${c}">${esc(t)}</span>`).join(' ')+
   `<div class="meta"><span>${p.id} · ${esc(p.file)}:${p.start} · ${p.words} 词</span><span>${p.replay.versions.length} 个版本${p.inserts.length?' · 大段加入':''}</span></div></div>`;}
document.getElementById('text').innerHTML=html;
document.querySelectorAll('.p').forEach(e=>e.onclick=()=>show(e.dataset.id));
"""


def render(record: dict) -> str:
    data = json.dumps(record, ensure_ascii=False).replace("</", "<\\/")
    rows = "".join(
        f"<tr><td>{html.escape(s['name'])}</td><td>{s['words']}</td>"
        + "".join(f"<td>{s['shares'][c]}%</td>" for c in ("human", "ai", "ai_edit", "pasted", "bulk", "uncommitted"))
        + "</tr>" for s in record["sections"])
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Writing Provenance</title>
<style>{STYLE}</style></head><body>
<header><h1>写作溯源</h1><div class="sub">{html.escape(record['main_tex'])} · {record['words']} 词 ·
{len(record['paragraphs'])} 段 · {record['status']}</div><div id="overall"></div><div class="legend" id="legend"></div>
<table><tr><th>节</th><th>词</th><th>作者</th><th>AI 生成</th><th>AI 修改</th><th>粘贴</th><th>大段</th><th>未提交</th></tr>{rows}</table>
</header>
<main><section id="text"></section><aside id="replay"><h3>回放</h3><div class="ver">点左边任意一段，查看它在各个提交中的版本。</div></aside></main>
<script type="application/json" id="data">{data}</script><script>{SCRIPT}</script></body></html>
"""


__all__ = ["render"]
