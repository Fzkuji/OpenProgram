"use client";

/**
 * "New" beside the wordmark once a check found an update
 * (desktop/main/self-update/update-service.js). A check only finds it: the
 * menu offers Download update, and once that downloaded and checked it,
 * Restart to update (readyVersion). Nothing shows in the browser or when
 * there is no update.
 */
import { useState } from "react";
import * as DM from "@radix-ui/react-dropdown-menu";

import { Badge } from "@/components/ui/badge";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import {
  GROUP_LABEL, MENU_ITEM_STATES, MENU_NOTE, MENU_NOTE_ERROR, MENU_PANEL, itemCls,
} from "@/components/ui/menu/menu-styles";
import { useDesktopBridge } from "@/lib/desktop/use-desktop-bridge";
import { useDesktopUpdateState } from "@/lib/desktop/use-desktop-update";
import { useTranslation } from "@/lib/i18n";
import { useSessionStore } from "@/lib/session-store";

const surface =
  MENU_PANEL +
  " z-(--z-popover) text-text-primary" +
  " data-[state=open]:animate-in data-[state=closed]:animate-out" +
  " data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" +
  " data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95";

export function UpdateBadge() {
  const { text } = useTranslation();
  const bridge = useDesktopBridge();
  const [state] = useDesktopUpdateState();
  const [open, setOpen] = useState(false);
  // The restart starts a new worker, which stops what is running now.
  const running = useSessionStore((s) => Object.keys(s.runningTasks).length > 0);
  const readyVersion = state?.readyVersion;
  const found = state?.release?.status === "available" ? state.release.latestVersion : null;
  const version = readyVersion || found;
  const updates = bridge?.updates;
  if (!version || !updates) return null;
  const install = updates.install;
  if (readyVersion && !install) return null;
  const installing = state?.status === "installing";
  const downloading = state?.status === "downloading";
  return (
    <DM.Root open={open} onOpenChange={setOpen} modal={false}>
      <DM.Trigger asChild>
        {/* Capitals at fs-2xs in a 16px pill: the pill spans the wordmark's
            cap height, top to baseline, and the letters sit at its centre
            (lowercase "New" read as riding high). */}
        <Badge
          asChild
          variant="accent"
          className="ml-1.5 h-4 px-1.5 py-0 text-fs-2xs leading-none font-semibold uppercase tracking-[0.05em] cursor-pointer pointer-events-auto"
        >
          <button
            type="button"
            aria-label={readyVersion
              ? text(`OpenProgram ${version} is ready`, `OpenProgram ${version} 已就绪`)
              : text(`OpenProgram ${version} is available`, `OpenProgram ${version} 可以更新`)}
          >
            New
          </button>
        </Badge>
      </DM.Trigger>
      <DM.Portal>
        <DM.Content align="start" sideOffset={6} className={surface + " min-w-[220px]"}>
          {readyVersion && install ? (
            <>
              <div className={GROUP_LABEL}>{text(`OpenProgram ${version} is ready`, `OpenProgram ${version} 已就绪`)}</div>
              <p className={MENU_NOTE + (running ? " " + MENU_NOTE_ERROR : "")}>
                {running
                  ? text(
                    "Downloaded and checked. A task is running now; restarting stops it.",
                    "已下载并校验。现在有任务在运行，重启会中断它。",
                  )
                  : text(
                    "Downloaded and checked. Restarting switches OpenProgram to it.",
                    "已下载并校验，重启后就换成新版本。",
                  )}
              </p>
              <DM.Item
                className={itemCls(false) + " " + MENU_ITEM_STATES}
                disabled={installing}
                onSelect={() => { void install(); }}
              >
                <MenuRow glyph="restart" label={text("Restart to update", "重启以更新")} />
              </DM.Item>
            </>
          ) : (
            <>
              <div className={GROUP_LABEL}>{text(`OpenProgram ${version} is available`, `OpenProgram ${version} 可以更新`)}</div>
              <DM.Item
                className={itemCls(false) + " " + MENU_ITEM_STATES}
                disabled={downloading}
                onSelect={(event) => { event.preventDefault(); void updates.download(); }}
              >
                <MenuRow
                  glyph="download-minimalistic"
                  label={state?.installsInPlace === false
                    ? text("Download and open installer", "下载并打开安装程序")
                    : text("Download update", "下载更新")}
                />
              </DM.Item>
            </>
          )}
          <DM.Item
            className={itemCls(false) + " " + MENU_ITEM_STATES}
            onSelect={() => { void updates.openRelease(); }}
          >
            <MenuRow glyph="document-text" label={text("What's new", "查看更新说明")} />
          </DM.Item>
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}
