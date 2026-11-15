"""Real layout acceptance for size decorations beside truncated filenames."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser
LONG_FILE = ".zcompdump.EXAMPLE-HOST.71212"
LONG_FOLDER = "a-long-directory-name-with-cached-size"


def test_long_names_preserve_visible_size_lane(tmp_path: Path) -> None:
    from playwright.sync_api import expect, sync_playwright

    bundle = tmp_path / "size-layout.js"
    script = r"""
const {build}=require('esbuild');
build({stdin:{contents:`
import React from 'react';import{createRoot}from'react-dom/client';
import{PierreFileTree}from'./components/files/explorer/pierre-file-tree';
function App(){
 const[selected,setSelected]=React.useState(null);
 const[expanded,setExpanded]=React.useState(new Set(['parent']));
 const entries=React.useMemo(()=>[
  {path:'.zcompdump.EXAMPLE-HOST.71212',type:'file',size:16912},
  {path:'empty.txt',type:'file',size:0},
  {path:'a-long-directory-name-with-cached-size',type:'dir',size:0},
  {path:'parent',type:'dir',size:0},
  {path:'parent/.zcompdump.EXAMPLE-HOST.71212',type:'file',size:16912},
 ],[]);
 return <div id="panel" style={{height:300,width:320}}><PierreFileTree projectId="layout"
 entries={entries} expanded={expanded} selected={selected} onExpandedChange={setExpanded}
 onSelect={setSelected} onOpen={()=>{}} onContextMenu={()=>{}}/></div>;
}createRoot(document.getElementById('root')).render(<App/>);`,resolveDir:process.cwd()+'/apps/web',loader:'tsx'},
bundle:true,jsx:'automatic',format:'iife',outfile:process.argv[1],plugins:[{name:'size-fixture',setup(b){
b.onResolve({filter:/^@\/lib\/i18n$/},()=>({path:'i18n',namespace:'translation'}));
b.onLoad({filter:/.*/,namespace:'translation'},()=>({contents:'export const useTranslation=()=>({text:en=>en,locale:"en"});',loader:'js'}));
b.onResolve({filter:/^\.\.\/file-management$/},()=>({path:'size',namespace:'fixture'}));
b.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:
'export const formatFileBytes=x=>({16912:"16.5 KiB",0:"0 B",1024:"1.0 KiB"}[x]); export const useFolderSize=()=>({value:{bytes:1024,state:"cached",complete:true}});',loader:'js'}));
}}]}).catch(e=>{console.error(e);process.exit(1)});
"""
    subprocess.run(["node", "-e", script, str(bundle)], cwd=ROOT, check=True)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content('<div id="root"></div>')
            page.add_script_tag(path=str(bundle))
            panel = page.locator("#panel")
            tree = page.locator("file-tree-container")
            for font_size in (13, 16):
                for width in (240, 320, 480):
                    panel.evaluate("(e,v)=>{e.style.width=v.width+'px';e.style.setProperty('--right-panel-text-size',v.font+'px');e.style.setProperty('--right-panel-meta-size',v.font+'px')}",
                                   {"width": width, "font": font_size})
                    for path, size in ((LONG_FILE, "16.5 KiB"), ("empty.txt", "0 B"),
                                       (LONG_FOLDER + "/", "1.0 KiB"),
                                       ("parent/" + LONG_FILE, "16.5 KiB")):
                        row = tree.locator(f'[data-item-path="{path}"]')
                        if path.endswith(LONG_FILE):
                            row.click()
                            expect(row).to_have_attribute("data-item-selected", "true")
                        decoration = row.locator('[data-item-section="decoration"] > span > span').first
                        expect(decoration).to_have_text(size)
                        expect(decoration).to_be_visible()
                        geometry = row.evaluate("""e=>{
                            const name=e.querySelector('[data-item-section="content"]').getBoundingClientRect();
                            const size=e.querySelector('[data-item-section="decoration"] > span > span').getBoundingClientRect();
                            const text=e.querySelector('[data-item-section="decoration"] > span > span');
                            return {nameRight:name.right,sizeLeft:size.left,sizeRight:size.right,
                                    rowRight:e.getBoundingClientRect().right,width:size.width,
                                    textWidth:text.clientWidth,textScrollWidth:text.scrollWidth};
                        }""")
                        assert geometry["width"] > 0, (path, width, font_size, geometry)
                        assert geometry["nameRight"] <= geometry["sizeLeft"] + 1, geometry
                        assert geometry["sizeRight"] <= geometry["rowRight"] + 1, geometry
                        assert geometry["textScrollWidth"] <= geometry["textWidth"] + 1, geometry
            # The fixture still uses the shared virtual tree after repeated resize/selection.
            expect(tree.get_by_role("treeitem")).to_have_count(5)
        finally:
            browser.close()
