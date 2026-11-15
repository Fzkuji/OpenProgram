"""Public virtual tree columns: geometry, metadata, scrolling and sort intent."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


def test_file_columns_align_and_scroll_with_real_virtual_rows(tmp_path: Path) -> None:
    from playwright.sync_api import expect, sync_playwright

    bundle = tmp_path / "columns.js"
    script = r"""
const {build}=require('esbuild');
build({stdin:{contents:`
import React from 'react';import{createRoot}from'react-dom/client';
import{PierreFileTree,PierreSearchTree}from'./components/files/explorer/pierre-file-tree';
function App(){
 const[sort,setSort]=React.useState('name:asc:folders:visible:tracked');
 const[selected,setSelected]=React.useState(null),[expanded,setExpanded]=React.useState(new Set(['parent']));
 const[search,setSearch]=React.useState(false),[deepView,setDeepView]=React.useState(false);
 const deepPaths=Array.from({length:10},(_,i)=>Array(i+1).fill('deep').join('/'));
 const entries=React.useMemo(()=>deepView?[...deepPaths.map(path=>({path,type:'dir',size:0})),{path:deepPaths[9]+'/deep-file.txt',type:'file',size:16912}]:[{path:'parent',type:'dir',size:0,mtime:1704067200},
 ...Array.from({length:80},(_,i)=>({path:'parent/'+String(i).padStart(3,'0')+'-a-very-long-filename.txt',type:'file',size:i===0?0:16912,mtime:1704067200})),
 {path:'.env',type:'file',size:0}],[deepView]);
 return <><button onClick={()=>{setDeepView(true);setExpanded(new Set(deepPaths))}}>Deep folders</button><button onClick={()=>setSearch(x=>!x)}>Toggle search</button><output>{sort}</output>
 <div id="panel" style={{height:300,width:320}}>{search?
 <PierreSearchTree projectId="columns" matches={[{path:'parent/child.txt',type:'file',size:0,mtime:1704067200}]} currentPath={null} onSelect={()=>{}} onOpen={()=>{}} onContextMenu={()=>{}}/>:
 <PierreFileTree key={deepView?"deep":"normal"} projectId="columns" entries={entries} expanded={expanded} selected={selected}
 sort={sort} onSortChange={setSort} onExpandedChange={setExpanded} onSelect={setSelected} onOpen={()=>{}} onContextMenu={()=>{}}/>}</div></>;
}createRoot(document.getElementById('root')).render(<App/>);`,resolveDir:process.cwd()+'/apps/web',loader:'tsx'},
bundle:true,jsx:'automatic',format:'iife',outfile:process.argv[1],plugins:[{name:'fixture',setup(b){
b.onResolve({filter:/^\.\.\/file-management$/},()=>({path:'size',namespace:'fixture'}));
b.onResolve({filter:/^@\/lib\/i18n$/},()=>({path:'i18n',namespace:'fixture'}));
b.onLoad({filter:/.*/,namespace:'fixture'},({path})=>({contents:path==='i18n'?
'export const useTranslation=()=>({text:en=>en,locale:"en"});':
'export const formatFileBytes=x=>x===0?"0 B":"16.5 KiB"; export const useFolderSize=()=>({value:{bytes:16912,state:"cached",complete:true}});',loader:'js'}));
}}]}).catch(e=>{console.error(e);process.exit(1)});
"""
    subprocess.run(["node", "-e", script, str(bundle)], cwd=ROOT, check=True)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page(timezone_id="UTC")
            page.set_content('<div id="root"></div>')
            page.add_script_tag(path=str(bundle))
            panel = page.locator("#panel")
            header = panel.get_by_role("row", name="File columns")
            expect(header.get_by_role("button", name="Name", exact=True)).to_be_visible()
            scroller = panel.locator("[data-file-tree-virtualized-scroll]")
            row = panel.locator('[data-item-path="parent/000-a-very-long-filename.txt"]')
            cells = row.locator('[data-item-section="decoration"] > span > span')
            expect(cells).to_have_count(3)
            expect(cells.nth(0)).to_have_text("0 B")
            expect(cells.nth(1)).to_contain_text("2024")
            expect(cells.nth(2)).to_have_text("TXT file")
            for font in (13, 16):
                for width in (240, 320, 480, 900):
                    panel.evaluate("(e,v)=>{e.style.width=v.width+'px';e.style.setProperty('--right-panel-text-size',v.font+'px');e.style.setProperty('--right-panel-meta-size',v.font+'px')}", {"width": width, "font": font})
                    scroller.evaluate("e=>{e.scrollLeft=0;e.scrollTop=0;e.dispatchEvent(new Event('scroll'))}")
                    page.wait_for_function("w=>Math.abs(document.querySelector('[data-file-columns-header]').clientWidth-w)<25", arg=width)
                    page.wait_for_timeout(50)
                    def bounds(locator):
                        return locator.evaluate("e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width}}")
                    view = bounds(panel)
                    size = bounds(cells.nth(0))
                    assert size["left"] >= view["left"] and size["right"] <= view["right"] + 1
                    assert bounds(row.locator('[data-item-section="content"]'))["right"] <= size["left"] + 1
                    assert abs(bounds(header.get_by_role("columnheader", name="Size", exact=True))["left"] - size["left"]) < 2
                    if width < 720:
                        # Below 720px only Name and Size fit; rows never scroll sideways.
                        expect(header.get_by_role("columnheader")).to_have_count(2)
                        expect(cells.nth(1)).to_be_hidden()
                        expect(cells.nth(2)).to_be_hidden()
                        assert scroller.evaluate("e=>e.scrollWidth<=e.clientWidth")
                    else:
                        for index, label in enumerate(("Date Modified", "Kind"), start=1):
                            assert abs(bounds(header.get_by_role("columnheader", name=label, exact=True))["left"] - bounds(cells.nth(index))["left"]) < 2
                        assert bounds(cells.nth(2))["right"] <= view["right"] + 1
            # Sorting only changes key/direction; display preferences survive.
            header.get_by_role("button", name="Size", exact=True).click()
            expect(page.locator("output")).to_have_text("size:desc:folders:visible:tracked")
            header.get_by_role("button", name="Size", exact=True).click()
            expect(page.locator("output")).to_have_text("size:asc:folders:visible:tracked")
            expect(header.get_by_role("columnheader", name="Size", exact=True)).to_have_attribute("aria-sort", "ascending")
            # Header stays fixed; sticky ancestor metadata stays column-aligned.
            top = bounds(header)["top"]
            scroller.evaluate("e=>{e.scrollTop=600;e.dispatchEvent(new Event('scroll'))}")
            expect(scroller).to_have_js_property("scrollTop", 600)
            assert bounds(header)["top"] == top
            sticky = panel.locator('[data-file-tree-sticky-row="true"]').first
            expect(sticky).to_be_visible()
            assert abs(bounds(sticky.locator('[data-item-section="decoration"] > span > span').nth(0))["left"] - bounds(header.get_by_role("columnheader", name="Size", exact=True))["left"]) < 2
            # Narrow sticky rows keep Size on the header's Size column without sideways scrolling.
            panel.evaluate("e=>e.style.width='320px'")
            page.wait_for_timeout(50)
            assert abs(bounds(sticky.locator('[data-item-section="decoration"] > span > span').nth(0))["left"] - bounds(header.get_by_role("columnheader", name="Size", exact=True))["left"]) < 2
            expect(scroller).to_have_js_property("scrollLeft", 0)
            # Deep hierarchy indentation stays within Name, even in narrow panels.
            panel.evaluate("e=>e.style.height='500px'")
            page.get_by_role("button", name="Deep folders").click()
            for width in (240, 320):
                panel.evaluate("(e,w)=>e.style.width=w+'px'", width)
                scroller.evaluate("e=>{e.scrollTop=0;e.scrollLeft=0;e.dispatchEvent(new Event('scroll'))}")
                deep = panel.locator('[data-item-path="' + '/'.join(["deep"] * 10) + '/deep-file.txt"]')
                expect(deep).to_be_visible()
                page.wait_for_timeout(50)
                deep_size = deep.locator('[data-item-section="decoration"] > span > span').first
                assert abs(bounds(deep_size)["left"] - bounds(header.get_by_role("columnheader", name="Size", exact=True))["left"]) < 2
                assert bounds(deep_size)["right"] <= bounds(panel)["right"] + 1
                assert bounds(deep.locator('[data-item-section="content"]'))["width"] >= 40
            panel.evaluate("e=>e.style.width='900px'")
            page.get_by_role("button", name="Toggle search").click()
            expect(header.get_by_role("button")).to_have_count(0)
            expect(header.get_by_role("columnheader")).to_have_count(4)
            synthetic = panel.locator('[data-item-path="parent/"]').locator('[data-item-section="decoration"] > span > span')
            expect(synthetic.nth(1)).to_have_text("")
            expect(synthetic.nth(2)).to_have_text("Folder")
        finally:
            browser.close()
