import React from "react";
import { createRoot } from "react-dom/client";
import * as documentDraftLifecycle from "../lib/state/file-drafts";
Object.assign(window, { documentDraftLifecycle });
import { FileTabPane } from "../components/center-tabs/file-tab-pane";

createRoot(document.getElementById("root")!).render(
  <FileTabPane projectId="p" path={new URLSearchParams(location.search).get("file") ?? "baseline.docx"} />,
);
