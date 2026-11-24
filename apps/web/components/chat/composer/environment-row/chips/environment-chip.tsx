"use client";

import { useEffect, useState } from "react";

import { HoverTip, TipBody } from "@/components/ui/tooltip";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { buttonVariants } from "@/components/ui/button";
import { MachineIcon, useMachineName } from "@/components/machines/machine-icon";
import { useSessionStore } from "@/lib/session-store";
import { closeAllPopovers } from "@/lib/runtime-bridge/ui";
import { useTranslation } from "@/lib/i18n";
import {
  LOCAL_ID,
  ensureMachines,
  findMachine,
  projectMachineId,
  useMachines,
} from "@/lib/machines/store";
import { cn } from "@/lib/utils";
import { useChatProject } from "../../../top-bar/use-chat-project";
import {
  GROUP_LABEL,
  MENU_NOTE,
  MENU_PANEL,
  MENU_SEPARATOR,
  itemCls,
} from "@/components/ui/menu/menu-styles";
import { MenuCheck } from "@/components/ui/menu/menu-check";

/** Environment chip — the machine this conversation runs its commands
 *  and file tools on. The project decides it: a project is a folder on
 *  one machine, and the home project is this computer. The chip shows
 *  that machine and does not switch; its menu shows the one machine
 *  checked and says which project set it. Sits in the env-chip row's
 *  leading slot (Claude's "Local" position).
 *
 *  The icon carries the machine's connection state as a dot. Its colour
 *  carries the worker connection tone (yellow connecting / paused), and
 *  red when the project's machine was removed. */
export function EnvironmentChip() {
  const { text } = useTranslation();
  const project = useChatProject();
  const machines = useMachines((s) => s.items);
  const kinds = useMachines((s) => s.kinds);
  const loaded = useMachines((s) => s.loaded);
  const statusBadge = useSessionStore((s) => s.statusBadge);
  const machineName = useMachineName();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    void ensureMachines();
  }, []);

  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  const envId = projectMachineId(project);
  const env = findMachine(machines, envId);
  const label = machineName(env, envId);
  const projectName = project?.name ?? "";
  const setBy = text(`Set by project ${projectName}`, `由项目 ${projectName} 决定`);

  // The link to the worker reconnects by itself; the chip does not narrate
  // it. Only what the user can act on colours the icon.
  const iconColor =
    !env && loaded
      ? "var(--accent-red)"
      : statusBadge.paused || statusBadge.tone === "warn"
        ? "var(--accent-yellow)"
        : undefined;

  const connection = statusBadge.paused ? text("Paused.", "已暂停。") : "";
  const where = env && env.id !== LOCAL_ID ? env.shell?.where ?? "" : "";
  const detail = [
    where,
    env
      ? text("Where commands and files run.", "命令和文件在这里执行。")
      : text("This project's device was removed; its tools cannot run.",
             "这个项目的设备已被删除，工具无法运行。"),
    projectName ? setBy : "",
    connection,
  ].filter(Boolean).join("\n");

  const tip = (
    <TipBody
      title={text(`Environment: ${label}`, `运行环境：${label}`)}
      detail={<span className="whitespace-pre-line">{detail}</span>}
    />
  );

  function onOpenChange(next: boolean) {
    if (next) {
      window.dispatchEvent(new Event("topbar-close-menus"));
      closeAllPopovers();
    }
    setOpen(next);
  }

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <HoverTip label={tip}>
        <PopoverTrigger asChild>
          <span
            role="button"
            className={cn(
              buttonVariants({ variant: "elevated", size: "sm" }),
              "status-badge cursor-pointer",
            )}
            data-environment-chip=""
          >
            <MachineIcon machine={env} kinds={kinds} fill style={{ color: iconColor }} />
            <span className="badge-short">{label}</span>
          </span>
        </PopoverTrigger>
      </HoverTip>
      <PopoverContent
        side="top"
        align="start"
        /* 10 = the environment row's band gap, as the project / folder /
           git popovers beside it. */
        sideOffset={10}
        className="w-auto border-0 bg-transparent p-0 shadow-none"
      >
        <div className={MENU_PANEL}>
          <div className={GROUP_LABEL}>{text("Environment", "运行环境")}</div>
          <div className={cn(itemCls(false), "cursor-default")} aria-disabled="true">
            <MachineIcon machine={env} kinds={kinds} fill />
            <span className="flex min-w-0 flex-1 items-center gap-[6px]">
              <span className="truncate">{label}</span>
              {where ? <span className="truncate text-text-muted">{where}</span> : null}
            </span>
            <MenuCheck />
          </div>
          {projectName ? (
            <>
              <div className={MENU_SEPARATOR} />
              <div className={MENU_NOTE}>{setBy}</div>
            </>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}
