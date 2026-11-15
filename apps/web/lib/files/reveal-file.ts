import { canBrowseLocalFiles } from "./local-file-url";

type Text = (en: string, zh: string) => string;

/** "Reveal in Finder" names the file manager of the machine it runs on. */
export function revealLabel(text: Text): string {
  const platform = typeof window === "undefined" ? undefined : window.openprogramDesktop?.platform;
  return platform === "darwin" ? text("Reveal in Finder", "在 Finder 中显示")
    : platform === "win32" ? text("Reveal in File Explorer", "在资源管理器中显示")
      : text("Reveal in File Manager", "在文件管理器中显示");
}

/** The worker selects the file in its own file manager, so this only
 *  makes sense when the worker runs on the machine the user is at. */
export function canRevealFiles(): boolean {
  return canBrowseLocalFiles();
}

/** Select an absolute path (a chat file) in the file manager. */
export async function revealAbsolutePath(path: string, sessionId?: string): Promise<boolean> {
  const params = `path=${encodeURIComponent(path)}${sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : ""}`;
  const response = await fetch(`/api/file-reveal?${params}`, { method: "POST" }).catch(() => null);
  return Boolean(response?.ok);
}
