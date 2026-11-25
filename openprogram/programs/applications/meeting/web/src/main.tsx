import { createRoot } from "react-dom/client";
import { MeetingWindow } from "./MeetingWindow";

// The host's theme and language arrive with openprogramApp's appearance and are
// applied to <html> before the first frame is drawn.
window.openprogramApp.ready.then(() => window.openprogramApp.appearance).finally(() => {
  createRoot(document.getElementById("root")!).render(<MeetingWindow />);
});
