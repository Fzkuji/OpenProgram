import "./app.css";
import { createRoot } from "react-dom/client";
import { ToastHost } from "@/components/ui/toast-host";
import { ConfirmDialogHost } from "@/components/ui/confirm-dialog";
import { App } from "./App";

// The kit's themes key on data-theme; the sandboxed page follows the system's light or dark.
const dark = window.matchMedia("(prefers-color-scheme: dark)");
const theme = () => {
  const mode = dark.matches ? "dark" : "light";
  document.documentElement.dataset.theme = mode;
  document.documentElement.dataset.themeMode = mode;
};
theme();
dark.addEventListener("change", theme);

createRoot(document.getElementById("root")!).render(<><App /><ToastHost /><ConfirmDialogHost /></>);
