"""The tab-server patch answers an empty tab list instead of failing on a missing header."""
import subprocess
from pathlib import Path


def test_empty_tab_response_does_not_dereference_a_missing_header():
    root = Path(__file__).resolve().parents[4]
    shim = root / "openprogram/programs/applications/browser/playwright_background_tabs.cjs"
    fixture = root / "tests/fixtures/browser/response-0079.cjs"
    script = r'''
const Module = require('module'), fs = require('fs');
const fixture = fs.readFileSync(process.argv[1], 'utf8');
function load() {
  const m = new Module('fixture');
  m.filename = '/fixture/playwright-core/lib/coreBundle.js';
  m._compile(fixture, m.filename);
  return m.exports;
}
function response() {
  return {_errors:[],_results:[],_code:[],_includeSnapshot:'explicit',
    _context:{config:{codegen:'none'},currentTab:()=>null,tabs:()=>[],debugger:()=>({pausedDetails:()=>null})}};
}
(async()=>{
  let failed=false;
  try {await load().call(response());} catch(e) {failed=e instanceof TypeError && e.message.includes("reading 'url'");}
  if (!failed) throw Error('original upstream reproduction missing');
  require(process.argv[2]);
  const result=await load().call(response());
  if (result.some(x=>x.isError) || result[0].title!=='Open tabs' || !result[0].content[0].includes('No open tabs.'))
    throw Error('empty-tab response must be a valid observation');
})();
'''
    subprocess.run(["node", "-e", script, str(fixture), str(shim)], check=True, capture_output=True, text=True)
