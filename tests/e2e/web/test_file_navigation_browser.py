"""Browser regression coverage for FileTree navigation history."""
from pathlib import Path
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import threading

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


def test_file_history_restores_files_tree_and_branches_forward_history(tmp_path: Path) -> None:
    from playwright.sync_api import expect, sync_playwright

    bundle = tmp_path / "file-navigation.js"
    script = r'''
const {build}=require("esbuild");
const mocks={
  "@/lib/files/file-drafts":'export const hasDocumentDraftsForPath=()=>false;',
  "@/lib/i18n":'export const useTranslation=()=>({text:(en)=>en,t:(key)=>key});',
  "@/lib/net/ws-request":`export const wsRequest=(action,payload)=>globalThis.__fileRequest(action,payload);
    export const wsMutationRequest=()=>Promise.resolve(null); export const reconcileWsMutation=()=>{};
    export const idempotencyKeyFor=()=>"test"; export class MutationRegistryCapacityError extends Error {}`,
  "@/lib/tabs/session-store":'export const useSessionStore=Object.assign((selector)=>selector({currentSessionId:null,activeChatKey:null,pendingProjectsByChat:{}}),{getState:()=>({currentSessionId:null})});',
  "@/components/ui/popover":'export const Popover=({children})=>children; export const PopoverAnchor=({children})=>children; export const PopoverContent=({children})=>children;',
  "@/components/sidebar/sessions-list/confirm-dialog":'export const ConfirmDialog=()=>null;',
  "@/components/sidebar/use-sidebar-menu":'export const useSidebarMenu=()=>({open:false,show:()=>{},close:()=>{}});',
  "./tree-context-menu":'export const TreeContextMenu=()=>null; export const treeClipboard={};',
  "./explorer-header":'export const ExplorerHeader=()=>null; export const copyText=()=>Promise.resolve();',
  "./file-tree-render":'export const InlineNameInput=()=>null;',
  "./file-management":`export const FileBreadcrumb=()=>null; export const FileDetails=()=>null; export const FileSortMenu=()=>null;
    export const useFileSort=()=>["name",()=>{}]; export const invalidateFolderSizes=()=>{}; export const formatFileBytes=x=>String(x);
    export const useFolderSize=()=>({value:{bytes:0,state:"ready"}});`,
  "@/components/ui/tooltip":'export const HoverTip=({children})=>children;',
};
build({stdin:{contents:`
import {setNavigate} from "./lib/navigate";
setNavigate(path=>window.history.pushState(null,"",path));
import React,{useEffect} from "react";import{createRoot}from"react-dom/client";
import{FileTree}from"./components/files/file-tree";import{useCenterTabs}from"./lib/tabs/center-tabs-store";
function App(){
 const active=useCenterTabs(s=>s.tabs.find(t=>t.id===s.activeId));
 const back=useCenterTabs(s=>s.navigateFileHistory), canBack=useCenterTabs(s=>s.canNavigateFile(-1));
 const forward=useCenterTabs(s=>s.navigateFileHistory), canForward=useCenterTabs(s=>s.canNavigateFile(1));
 useEffect(()=>{useCenterTabs.getState().openBuiltinTab("files")},[]);
 return <><button aria-label="Back" disabled={!canBack} onClick={()=>back(-1)}>Back</button><button aria-label="Forward" disabled={!canForward} onClick={()=>forward(1)}>Forward</button>
  {active?.kind==="builtin"&&active.page==="files"?<main data-page="files"><FileTree projectId="project" central/></main>:null}
  {active?.kind==="file"?<main data-page="file"><output data-file-path="active">{active.path}</output></main>:null}
  <output data-active-kind="active">{active?.kind??"none"}</output></>;
}
globalThis.__fileRequest=(action,payload)=>{
 if(action==="list_projects") return Promise.resolve({session_id:null,projects:[{id:"project",path:"/private/tmp/file-navigation-fixture",is_default:true}]});
 if(action!=="project_file_tree") return Promise.resolve(null);
 const entries=payload.path===""?[{name:"folder",type:"dir",size:0,mtime:1},...Array.from({length:40},(_,i)=>({name:"00-"+String(i).padStart(2,"0")+".txt",type:"file",size:1,mtime:1})),{name:"01-after.txt",type:"file",size:1,mtime:1}]:[...Array.from({length:40},(_,i)=>({name:"inside-"+String(i).padStart(2,"0")+".txt",type:"file",size:1,mtime:1})),{name:"A.txt",type:"file",size:1,mtime:1},{name:"B.txt",type:"file",size:1,mtime:1},...Array.from({length:20},(_,i)=>({name:"tail-"+String(i).padStart(2,"0")+".txt",type:"file",size:1,mtime:1}))];
 return Promise.resolve({project_id:"project",path:payload.path,entries:payload.path&&!payload.cursor?entries.slice(0,30):payload.cursor?entries.slice(30):entries,snapshot_id:"snapshot",next_cursor:payload.path&&!payload.cursor?"more":null});
};
createRoot(document.getElementById("root")).render(<App/>);`,resolveDir:process.cwd()+"/apps/web",loader:"tsx"},bundle:true,jsx:"automatic",format:"iife",platform:"browser",outfile:process.argv[1],tsconfig:process.cwd()+"/apps/web/tsconfig.json",plugins:[{name:"mock-modules",setup(b){
 for(const [specifier,contents] of Object.entries(mocks)) b.onResolve({filter:new RegExp("^"+specifier.replace(/[.*+?^${}()|[\\]\\\\]/g,"\\\\$&")+"$")},()=>({path:specifier,namespace:"mock"}));
 b.onLoad({filter:/.*/,namespace:"mock"},args=>({contents:mocks[args.path],loader:"js"}));
}}, {name:"css",setup(b){b.onLoad({filter:/\\.css$/},()=>({contents:"",loader:"css"}));}}]}).catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run(["node", "-e", script, str(bundle)], cwd=ROOT, check=True)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(bundle.parent)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            page = browser.new_page(viewport={"width": 900, "height": 420})
            page.set_default_timeout(5000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.set_content('<style>main[data-page=files]{height:360px;width:600px}main[data-page=files]>div{height:100%;display:flex;flex-direction:column}</style><div id="root"></div>')
            page.evaluate('history.replaceState(null,"","/s/file-navigation-origin")')
            page.add_style_tag(path=str(bundle.with_suffix(".css")))
            page.add_script_tag(path=str(bundle))
            assert not errors, errors
            files = page.locator('[data-page="files"]')
            expect(files).to_be_visible()
            expect(page.get_by_role("treeitem", name="folder", exact=True)).to_be_visible()

            folder = page.get_by_role("treeitem", name="folder", exact=True)
            folder.click()
            expect(folder).to_have_attribute("aria-expanded", "true")
            scroller = page.locator("[data-file-tree-virtualized-scroll]")
            scroller.evaluate("e=>{e.scrollTop=750;e.dispatchEvent(new Event('scroll'))}")
            expect(page.get_by_role("treeitem", name="A.txt", exact=True)).to_be_attached()
            scroller.evaluate("e=>{e.scrollTop=1000;e.dispatchEvent(new Event('scroll'))}")
            expect(scroller).to_have_js_property("scrollTop", 1000)
            expect(page.locator("[data-is-scrolling]")).to_have_count(0)
            bounds = page.get_by_role("treeitem", name="A.txt", exact=True).bounding_box()
            page.mouse.click(bounds["x"] + 100, bounds["y"] + 15)
            expect(page.locator('[data-page="file"]')).to_be_visible()
            expect(page.locator('[data-file-path="active"]')).to_have_text("folder/A.txt")
            assert page.url.endswith("/s/file-navigation-origin"), "opening a file must retain its conversation route"

            page.get_by_role("button", name="Back", exact=True).click()
            expect(files).to_be_visible()
            expect(page.get_by_role("treeitem", name="A.txt", exact=True)).to_be_visible()
            expect(scroller).to_have_js_property("scrollTop", 1000)
            page.get_by_role("button", name="Forward", exact=True).click()
            expect(page.locator('[data-page="file"]')).to_be_visible()
            expect(page.locator('[data-file-path="active"]')).to_have_text("folder/A.txt")
            assert page.url.endswith("/s/file-navigation-origin"), "opening a file must retain its conversation route"

            page.get_by_role("button", name="Back", exact=True).click()
            page.get_by_role("button", name="Back", exact=True).click()
            expect(files).to_be_visible()
            page.get_by_role("button", name="Forward", exact=True).click()
            expect(scroller).to_have_js_property("scrollTop", 1000)
            expect(page.locator("[data-is-scrolling]")).to_have_count(0)
            bounds = page.get_by_role("treeitem", name="B.txt", exact=True).bounding_box()
            page.mouse.click(bounds["x"] + 100, bounds["y"] + 15)
            expect(page.locator('[data-file-path="active"]')).to_have_text("folder/B.txt")
            expect(page.get_by_role("button", name="Forward", exact=True)).to_be_disabled()
            page.get_by_role("button", name="Back", exact=True).click()
            expect(files).to_be_visible()
            page.get_by_role("button", name="Back", exact=True).click()
            expect(files).to_be_visible()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
            browser.close()
