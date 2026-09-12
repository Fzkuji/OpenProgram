import React from "react";
import { createRoot } from "react-dom/client";
import { FileViewer } from "../components/files/file-viewer";
const root = createRoot(document.getElementById("root")!);
Object.assign(window, {
  showFile(path: string, encoded?: string) {
    const bytes = encoded ? new Blob([Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0))]) : undefined;
    root.render(<FileViewer projectId="p" path={path} sourceBlob={bytes}
      snapshot={{ project_id: "p", path, binary: true, size: bytes?.size ?? 0, mtime: 0 }} />);
  },
  hideFile() { root.render(null); },
});
