import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import * as documentDraftLifecycle from "../lib/state/file-drafts";
import { closeDocumentController } from "../lib/state/document-controller";
import { FileTabPane } from "../components/center-tabs/file-tab-pane";
Object.assign(window, { documentDraftLifecycle });
function Fixture() {
  const [closed,setClosed]=useState(false);
  const [hidden,setHidden]=useState(false);
  const [settings,setSettings]=useState(false);
  const path=new URLSearchParams(location.search).get("file") ?? "notes.md";
  return <><button onClick={async()=>{try {await closeDocumentController({kind:"project",projectId:"p",path});setClosed(true);} catch {/* The real controller displays the retained error. */}}} >Close file</button>
    <button onClick={()=>setHidden(!hidden)}>{hidden ? "Return to file" : "Other tab"}</button>
    <button onClick={()=>{history.pushState({},"",`${settings ? "/" : "/settings"}${location.search}`);setSettings(!settings);}}>{settings ? "Back to file route" : "Open settings route"}</button>
    {closed ? <span>File closed</span> : <div style={{display:hidden || settings ? "none" : "block"}}><FileTabPane projectId="p" path={path}/></div>}
  </>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
