// Screenshots the user sends to the conversation from the screenshot tool
// (apps/desktop/main/windows/screenshot.js): the main process hands them to
// the App window the user was last in, and they go into the message box of
// the conversation that window shows, as if dropped on it.
import { queueComposerFiles } from "@/lib/chat/composer-inbox";
import { useSessionStore } from "@/lib/session-store";
import type { DesktopBridge } from "./bridge-api";

export function subscribeScreenshots(bridge: DesktopBridge): void {
  bridge.screenshot?.onAttach((shot) => {
    if (!shot?.png) return;
    // A new chat has no key yet; its composer takes the files queued under null.
    queueComposerFiles(useSessionStore.getState().activeChatKey, [new File([shot.png as BlobPart], shot.name || "Screenshot.png", { type: "image/png" })]);
  });
}
