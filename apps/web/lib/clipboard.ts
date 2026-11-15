import { desktopBridge } from "./desktop/bridge/bridge-api";

/** Native menu callbacks may run after browser clipboard activation expires. */
export async function copyText(value: string): Promise<boolean> {
  try {
    const native = desktopBridge()?.writeClipboardText;
    if (native) { await native(value); return true; }
  } catch { /* Fall back for older or unavailable desktop bridges. */ }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch { /* Browser permission denied: use the selection fallback. */ }
  const active = document.activeElement as HTMLElement | null;
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.style.cssText = "position:fixed;opacity:0;left:0;top:0";
  document.body.appendChild(textarea);
  try { textarea.focus(); textarea.select(); return document.execCommand("copy"); }
  catch { return false; }
  finally { textarea.remove(); active?.focus({ preventScroll: true }); }
}
