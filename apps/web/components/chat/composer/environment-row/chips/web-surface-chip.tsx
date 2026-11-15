"use client";

import { SolarIcon } from "@/components/solar-icons";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { hidePagePreview, restorePagePreview } from "@/lib/browser/browser-resource-projection";
import {
  pipCoversCenter,
  useWebTabPip,
} from "@/lib/browser/web-tab/web-tab-pip-store";
import { surfaceRefForChat } from "@/lib/desktop/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import styles from "../environment-row.module.css";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** The web page this chat is paired with. The agent reads and operates it
 *  through the tools its Agent configuration grants. The chip also toggles
 *  the floating preview: it closes the preview floating over this chat, and
 *  brings it back while the page still exists in the background and is not
 *  already the active/split page. It names one page and acts on that page
 *  only: in a split view the chat reads the page in the other pane
 *  (surfaceRefForChat), so its chip names that page, which is on screen
 *  already, and leaves the preview of another page alone. */
export function WebSurfaceChip({ sessionId }: { sessionId: string | null }) {
  const { text } = useTranslation();
  const activeId = useCenterTabs((state) => state.activeId);
  useCenterTabs((state) => state.splitWebTabId);
  const tabs = useCenterTabs((state) => state.tabs);
  const groups = useCenterTabs((state) => state.groups);
  const pipTabId = useWebTabPip((s) => s.tabId);
  const backgroundTabId = useWebTabPip((s) => s.backgroundTabId);
  const backgroundOwnerTabId = useWebTabPip((s) => s.backgroundOwnerTabId);
  const pipSessionId = useWebTabPip((s) => s.ownerSessionId);
  const surface = surfaceRefForChat(sessionId);
  const restoreId = !pipTabId && backgroundTabId && backgroundOwnerTabId
    && activeId === backgroundOwnerTabId
    && pipCoversCenter(backgroundTabId, backgroundOwnerTabId, { tabs, activeId, groups })
    ? backgroundTabId
    : null;
  const floating = !!pipTabId && !!sessionId && pipSessionId === sessionId;
  if (!surface && !restoreId && !floating) return null;

  // The page the chip names, which the preview folds into (web-tab-pip-motion.ts).
  const names = surface?.tab_id ?? restoreId ?? (floating ? pipTabId : null) ?? "";
  const toggle = restoreId === names ? "restore" : floating && pipTabId === names ? "close" : null;
  const restoreTab = toggle ? tabs.find((tab) => tab.id === names) : undefined;
  const url = surface?.url || (restoreTab?.kind === "web" ? restoreTab.url : "");
  const title =
    surface?.title || restoreTab?.title || new URL(url || "about:blank").hostname || "Web";
  const className =
    `${cn(buttonVariants({ variant: "elevated", size: "sm" }))} status-badge ${styles.surfaceChip}`;
  const body = (
    <>
      <SolarIcon name="earth" size={16} fit="fill" aria-hidden="true" />
      <span className={styles.surfaceChipLabel}>{title}</span>
    </>
  );
  return (
    <HoverTip
      label={
        <TipBody
          title={text(`Web page: ${title}`, `网页：${title}`)}
          detail={toggle === "restore"
            ? text(
              "The agent can read and operate this page. Click to show it as a floating preview.",
              "agent 可以读取并操作这个网页。点击以浮动预览显示。",
            )
            : toggle === "close"
              ? text(
                "The agent can read and operate this page. Click to close the floating preview.",
                "agent 可以读取并操作这个网页。点击收起浮动预览。",
              )
              : text("The agent can read and operate this page.", "agent 可以读取并操作这个网页。")}
        />
      }
    >
      {toggle === "restore" && restoreId && backgroundOwnerTabId ? (
        <button
          type="button"
          className={className}
          data-web-surface-chip={names}
          aria-label={text(`Show page preview: ${title}`, `显示网页预览：${title}`)}
          onClick={() => restorePagePreview(restoreId, backgroundOwnerTabId)}
        >
          {body}
        </button>
      ) : toggle === "close" ? (
        <button
          type="button"
          className={className}
          data-web-surface-chip={names}
          aria-label={text(`Close page preview: ${title}`, `收起网页预览：${title}`)}
          onClick={hidePagePreview}
        >
          {body}
        </button>
      ) : (
        <span
          className={className}
          data-web-surface-chip={names}
          aria-label={text(`Agent can access: ${title}`, `Agent 可访问：${title}`)}
        >
          {body}
        </span>
      )}
    </HoverTip>
  );
}
