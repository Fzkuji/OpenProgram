"use client";

import { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";

import { SolarIcon } from "@/components/solar-icons";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { buttonVariants } from "@/components/ui/button";
import { useSessionStore } from "@/lib/session-store";
import { useSessionScope } from "@/lib/session-store/session-scope";
import { closeAllPopovers } from "@/lib/runtime-bridge/ui";
import { useTranslation } from "@/lib/i18n";
import {
  ensureEnvironments,
  environmentIconName,
  environmentWhere,
  findEnvironment,
  useEnvironments,
  type EnvironmentInfo,
} from "@/lib/environments/store";
import { cn } from "@/lib/utils";
import { useBoundChat } from "../../../top-bar/bound-chat";
import {
  CHECK_SLOT_PAD,
  GROUP_LABEL,
  MENU_NOTE,
  MENU_PANEL,
  MENU_SEPARATOR,
  itemCls,
} from "@/components/ui/menu/menu-styles";
import { MenuCheck } from "@/components/ui/menu/menu-check";

/** Environment chip — where this conversation runs its commands and
 *  file tools (this computer, an SSH host, a Docker container). Sits in
 *  the env-chip row's leading slot (Claude's "Local" position).
 *
 *  A draft picks freely; the pick rides the first message and the
 *  worker pins it. After that the menu still opens, with the other
 *  environments greyed out and a line saying why. The icon's colour
 *  carries the worker connection tone (yellow connecting / paused, red
 *  disconnected), as the old status chip did. */
export function EnvironmentChip() {
  const { text } = useTranslation();
  const { sessionId } = useBoundChat();
  const items = useEnvironments((s) => s.items);
  const defaultId = useEnvironments((s) => s.default);
  const loaded = useEnvironments((s) => s.loaded);
  const statusBadge = useSessionStore((s) => s.statusBadge);
  const pinnedId = useSessionStore((s) =>
    sessionId ? s.conversations[sessionId]?.environment_id ?? null : null);
  const draftPick = useSessionScope((s) => s.settings.environment);
  const patchSettings = useSessionScope((s) => s.patchSettings);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    void ensureEnvironments();
  }, []);

  // A draft's pick that was since removed falls back to the default
  // instead of failing the first send.
  useEffect(() => {
    if (!sessionId && loaded && draftPick && !findEnvironment(items, draftPick)) {
      patchSettings({ environment: undefined });
    }
  }, [sessionId, loaded, draftPick, items, patchSettings]);

  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  const locked = !!sessionId;
  const envId = (locked ? pinnedId : draftPick) || defaultId;
  const env: EnvironmentInfo | null = findEnvironment(items, envId);
  const label = env ? env.name : envId;

  const iconColor =
    statusBadge.tone === "err" || (locked && !env && loaded)
      ? "var(--accent-red)"
      : statusBadge.paused || statusBadge.tone === "warn" || statusBadge.tone === "connecting"
        ? "var(--accent-yellow)"
        : undefined;

  const connection =
    statusBadge.tone === "err"
      ? text("Disconnected from the worker.", "与后台的连接已断开。")
      : statusBadge.tone === "connecting"
        ? text("Connecting to the worker…", "正在连接后台…")
        : statusBadge.paused
          ? text("Paused.", "已暂停。")
          : "";
  const where = env && env.kind !== "local" ? environmentWhere(env) : "";
  const detail = [
    where,
    locked
      ? env
        ? text("Fixed for this conversation. Start a new chat to use another environment.",
               "这个会话的环境已固定。新建会话可以选别的环境。")
        : text("This conversation's environment was removed; its tools cannot run.",
               "这个会话的环境已被删除，工具无法运行。")
      : text("Where commands and files run. Click to choose before the first message.",
             "命令和文件在这里执行。发出第一条消息前可以点击切换。"),
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

  function choose(id: string) {
    if (locked) return;
    setOpen(false);
    patchSettings({ environment: id === defaultId ? undefined : id });
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
            <SolarIcon
              name={environmentIconName(env?.kind)}
              size={16}
              fit="fill"
              style={{ color: iconColor }}
              aria-hidden="true"
            />
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
          {locked ? (
            <div className={MENU_NOTE}>
              {text("Fixed for this conversation. Start a new chat to use another environment.",
                    "这个会话的环境已固定。新建会话可以选别的环境。")}
            </div>
          ) : null}
          {items.map((item) => (
            <div
              key={item.id}
              className={cn(
                itemCls(false),
                locked && item.id !== envId && "pointer-events-none opacity-50",
                locked && item.id === envId && "cursor-default",
              )}
              aria-disabled={locked || undefined}
              onClick={() => choose(item.id)}
            >
              <SolarIcon name={environmentIconName(item.kind)} size={16} fit="fill" aria-hidden="true" />
              <span className="flex min-w-0 flex-1 items-center gap-[6px]">
                <span className="truncate">{item.name}</span>
                {item.kind !== "local" ? (
                  <span className="truncate text-text-muted">{environmentWhere(item)}</span>
                ) : null}
              </span>
              {item.id === envId ? (
                <MenuCheck />
              ) : (
                <span className={CHECK_SLOT_PAD} />
              )}
            </div>
          ))}
          <div className={MENU_SEPARATOR} />
          <a
            href="/settings/environments"
            onClick={() => setOpen(false)}
            className={`${itemCls(false)} no-underline`}
          >
            <SolarIcon name="settings-minimalistic" size={16} fit="fill" aria-hidden="true" />
            <span className="flex-1 truncate">{text("Add environments", "添加环境")}</span>
            <ChevronRight className="menu-row-chevron size-4 shrink-0 text-text-muted" aria-hidden="true" />
          </a>
        </div>
      </PopoverContent>
    </Popover>
  );
}
