import React from "react";
import { createRoot } from "react-dom/client";
import { DocumentWindow } from "../components/files/document-window";

createRoot(document.getElementById("root")!).render(<DocumentWindow projectId="p" path={new URLSearchParams(location.search).get("file") ?? "notes.md"} />);
