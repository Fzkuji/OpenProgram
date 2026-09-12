"""Native virtual tree scroll restoration through its shared component handle."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


def test_file_tree_restores_anchor_without_refocusing_first_row(tmp_path: Path) -> None:
    from playwright.sync_api import expect, sync_playwright

    bundle = tmp_path / "tree.js"
    script = r"""
const {build}=require('esbuild');
build({stdin:{contents:`
import React from 'react';import{createRoot}from'react-dom/client';
import{PierreFileTree}from'./components/files/pierre-file-tree';
function App(){
 const ref=React.useRef(null),saved=React.useRef(null);
 const[revision,setRevision]=React.useState(0),[selected,setSelected]=React.useState(null);
 const[expanded,setExpanded]=React.useState(new Set());
 const rows=React.useMemo(()=>Array.from({length:200},(_,i)=>({path:'file-'+String(i).padStart(3,'0')+'.txt',type:'file',size:revision})),[revision]);
 return <><button onClick={()=>{saved.current=ref.current.getScrollState()}}>Remember</button>
 <button onClick={()=>ref.current.scrollToTop()}>Top</button>
 <button onClick={()=>ref.current.restoreScrollState(saved.current)}>Restore</button>
 <button onClick={()=>setRevision(x=>x+1)}>Refresh metadata</button>
 <div style={{height:400,width:400}}><PierreFileTree ref={ref} projectId="test" entries={rows} expanded={expanded} selected={selected} onExpandedChange={setExpanded} onSelect={setSelected} onOpen={()=>{}} onContextMenu={()=>{}}/></div><output>{revision}</output></>;
}createRoot(document.getElementById('root')).render(<App/>);`,resolveDir:process.cwd()+'/apps/web',loader:'tsx'},bundle:true,jsx:'automatic',format:'iife',outfile:process.argv[1],plugins:[{name:'metadata',setup(b){
b.onResolve({filter:/^\.\/file-management$/},()=>({path:'meta',namespace:'fixture'}));
b.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:'export const formatFileBytes=x=>String(x); export const useFolderSize=()=>({value:{bytes:0,state:"ready"}});',loader:'js'}));
}}]}).catch(e=>{console.error(e);process.exit(1)});
"""
    subprocess.run(["node", "-e", script, str(bundle)], cwd=ROOT, check=True)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content('<div id="root"></div>')
            page.add_script_tag(path=str(bundle))
            page.get_by_role("treeitem", name="file-000.txt", exact=True).click()
            scroller = page.locator("[data-file-tree-virtualized-scroll]")
            scroller.evaluate("e=>{e.scrollTop=1537;e.dispatchEvent(new Event('scroll'))}")
            expect(scroller).to_have_js_property("scrollTop", 1537)
            page.get_by_role("button", name="Remember", exact=True).click()
            page.get_by_role("button", name="Top", exact=True).click()
            expect(scroller).to_have_js_property("scrollTop", 0)
            page.get_by_role("button", name="Restore", exact=True).click()
            expect(scroller).to_have_js_property("scrollTop", 1537)
            page.get_by_role("button", name="Refresh metadata", exact=True).click()
            expect(page.locator("output")).to_have_text("1")
            expect(scroller).to_have_js_property("scrollTop", 1537)
        finally:
            browser.close()
