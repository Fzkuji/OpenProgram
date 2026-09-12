import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { closeDocumentController } from "../lib/state/document-controller";
import { DocumentWindow } from "../components/files/lazy-document-window";
import { PersistentFilePanes } from "../components/center-tabs/persistent-file-panes";

function Fixture() {
  const [closed, setClosed] = useState(false);
  const [visible, setVisible] = useState(true);
  const [settings, setSettings] = useState(false);
  const [split, setSplit] = useState(false);
  const [swapped, setSwapped] = useState(false);
  const path = new URLSearchParams(location.search).get("file") ?? "baseline.docx";
  if (new URLSearchParams(location.search).has("attachment")) return <DocumentWindow projectId="" path={path} sessionId="fixture-session" readOnly />;
  const tabs = [{id:"file",kind:"file",projectId:"p",path}, {id:"peer",kind:"file",projectId:"p",path:"baseline.pptx"}];
  const layouts = new Map([
    ["file", {className: "fixture-pane", style: {order: swapped ? 2 : 0, width: split ? "50%" : "100%"}}],
    ["peer", {className: "fixture-pane", style: {order: swapped ? 0 : 2, width: "50%"}}],
  ]);
  const navigate = (next: boolean) => {
    history.pushState({}, "", `${next ? "/settings" : "/"}${location.search}`);
    setSettings(next);
  };
  return <div style={{height:"100%",display:"flex",flexDirection:"column"}}><button onClick={async () => {
    await closeDocumentController({ kind: "project", projectId: "p", path });
    setClosed(true);
  }}>Close file</button><button onClick={() => navigate(!settings)}>{settings ? "Back to file route" : "Open settings route"}</button><button onClick={() => setVisible(v => !v)}>{visible ? "Other tab" : "Return to file"}</button><button onClick={() => setSplit(v=>!v)}>Split files</button><button onClick={() => setSwapped(v=>!v)}>Swap panes</button>{closed ? <span>File closed</span> : <div style={{flex:1,minHeight:0,display:settings ? "none" : "flex"}}><PersistentFilePanes tabs={swapped ? [...tabs].reverse() : tabs} activeFileIds={new Set(visible ? split ? ["file","peer"] : ["file"] : [])} layouts={layouts} /></div>}</div>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
