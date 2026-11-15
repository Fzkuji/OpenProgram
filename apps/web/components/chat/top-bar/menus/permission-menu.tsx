"use client";

/**
 * PermissionBadge — top-bar chip + dropdown for the per-session
 * permission mode. Same chip pattern as <AgentBadge> (runtime-badge span
 * as PopoverTrigger, PopoverContent dropdown, `topbar-close-menus` mutual
 * exclusion, animated iconRef). Lives in the top-bar `.right` region.
 *
 * The dropdown follows the Claude selection-menu grammar: a "Mode"
 * GROUP_LABEL header, then the permission tiers (the default, Bypass,
 * first) as two-line itemCls rows, a muted description under each label —
 * a right-aligned ink Check on the currently-selected tier only (hover
 * tint is the only row background; selection is never a filled row),
 * then the number shortcut hint in muted text. The check / shortcut column is centred on the label line so it
 * lines up with the single-line rows of the other menus.
 * Picking a tier switches to it and closes the menu.
 *
 * State comes from usePermissionMode() (the same per-session store the
 * old composer plus-menu item read/wrote), so switching here is picked
 * up everywhere immediately.
 */
import { useEffect, useRef, useState } from "react";

import { useTranslation } from "@/lib/i18n";
import { closeAllPopovers } from "@/lib/runtime-bridge/ui";

import { type AnimatedNavIconHandle } from "@/components/animated-icons";
import { SolarIcon } from "@/components/solar-icons";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import {
  usePermissionMode,
  type PermissionMode,
} from "../../composer/controls/toggles/use-permission-mode";
import {
  CHECK_SLOT_PAD,
  GROUP_LABEL,
  ITEM_DESC,
  ITEM_TITLE,
  ITEM_TWO_LINE,
  MENU_PANEL,
  MENU_TAG,
  MENU_TRAILING,
  SHORTCUT,
  itemCls,
} from "@/components/ui/menu/menu-styles";
import { MenuCheck } from "@/components/ui/menu/menu-check";

export function PermissionBadge() {
  const { text } = useTranslation();
  const { mode, options, set, pending, error } = usePermissionMode();
  const [open, setOpen] = useState(false);
  const iconRef = useRef<AnimatedNavIconHandle>(null);

  // A `topbar-close-menus` event (fired by another top-bar dropdown)
  // closes this menu, so only one is ever open.
  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  function onOpenChange(next: boolean) {
    if (next) {
      window.dispatchEvent(new Event("topbar-close-menus"));
      closeAllPopovers();
    }
    setOpen(next);
  }

  const current = options.find((o) => o.value === mode);
  const label = current?.label ?? mode;

  function pick(value: PermissionMode) {
    if (pending) return;
    set(value);
    setOpen(false);
  }

  return (
    <>
      <Popover open={open} onOpenChange={onOpenChange}>
        <HoverTip
          label={
            <TipBody
              title={text(`Permissions: ${label}`, `权限：${label}`)}
              detail={current?.description ?? text("What the agent may do without asking. Click to change.", "agent 不经询问可以做什么。点击更改。")}
            />
          }
        >
          <PopoverTrigger asChild>
            <button
              type="button"
              id="permissionBadge"
              className={`${cn(buttonVariants({ variant: "elevated", size: "sm" }))} runtime-badge permission-badge`}
              onMouseEnter={() => iconRef.current?.startAnimation?.()}
              onMouseLeave={() => iconRef.current?.stopAnimation?.()}
            >
              <SolarIcon
                name="shield-check"
                ref={iconRef}
                size={16}
                fit="fill"
                className="shrink-0"
                aria-hidden="true"
              />
              <span className="badge-details" aria-live="polite">{pending ? text("Updating permissions…", "正在更新权限…") : label}</span>
            </button>
          </PopoverTrigger>
        </HoverTip>
        <PopoverContent
          align="start"
          side="top"
          /* 9 = 10px band gap − 1px：输入框的 ring 画在盒外 1px，弹层
             再下沉 1px 才与可见边缘完全贴合（底部一排弹层统一规则）。 */
          sideOffset={9}
          onOpenAutoFocus={(e) => e.preventDefault()}
          className="w-auto border-0 bg-transparent p-0 shadow-none"
        >
          <div className={MENU_PANEL}>
            <div className={GROUP_LABEL}>{text("Mode", "模式")}</div>
            {options.map((o) => (
              <div
                key={o.value}
                // 选中不铺底色（hover 是唯一底色），选中态只靠右侧勾。
                // 带描述的行是两行行（ITEM_TWO_LINE：2.5px / 4px 竖衬 → 40.5px）。
                className={itemCls(false) + (o.description ? ` ${ITEM_TWO_LINE}` : "")}
                onClick={() => pick(o.value)}
              >
                <span className="min-w-0 flex-1">
                  {/* Title line: label, then the "Default" tag on the
                      session's default mode. */}
                  <span className="flex items-center gap-[8px]">
                    <span className={o.description ? ITEM_TITLE : "truncate"}>{o.label}</span>
                    {o.isDefault ? (
                      <span className={MENU_TAG}>{text("Default", "默认")}</span>
                    ) : null}
                  </span>
                  {o.description ? (
                    <span className={ITEM_DESC}>{o.description}</span>
                  ) : null}
                </span>
                {/* Claude 顺序：勾在前、数字快捷键在最右。未选中留同宽
                    占位保持数字列对齐；整列一行标题高，落在标题行上。 */}
                <span className={MENU_TRAILING}>
                  {o.value === mode ? (
                    <MenuCheck />
                  ) : (
                    <span className={CHECK_SLOT_PAD} />
                  )}
                  {o.key ? <span className={SHORTCUT}>{o.key}</span> : null}
                </span>
              </div>
            ))}
          </div>
        </PopoverContent>
      </Popover>

      {error ? <span role="alert" className="text-fs-xs text-danger">{error}</span> : null}
    </>
  );
}
