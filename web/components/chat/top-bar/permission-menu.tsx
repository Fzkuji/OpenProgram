"use client";

/**
 * PermissionBadge — top-bar chip + dropdown for the per-session
 * permission mode. Same chip pattern as <AgentBadge> (runtime-badge span
 * as PopoverTrigger, PopoverContent dropdown, `topbar-close-menus` mutual
 * exclusion, animated iconRef). Lives in the top-bar `.right` region.
 *
 * The dropdown follows the Claude selection-menu grammar: a "Mode"
 * GROUP_LABEL header, then the 5 permission tiers as plain 14px rows —
 * number shortcut hint right-aligned in muted text (1/2/3/4; bypass has
 * none), and a right-aligned ink Check on the currently-selected tier
 * only (hover tint is the only row background; selection is never a
 * filled row). The danger-tier colour lives on the topbar chip, not in
 * the menu rows.
 * Picking a tier switches to it and closes the menu. Picking `bypass`
 * (when not already bypass) opens a confirmation dialog instead — the
 * switch only lands after the user confirms.
 *
 * State comes from usePermissionMode() (the same per-session store the
 * old composer plus-menu item read/wrote), so switching here is picked
 * up everywhere immediately.
 */
import { createPortal } from "react-dom";
import { useEffect, useRef, useState } from "react";

import { useTranslation } from "@/lib/i18n";
import { Check } from "lucide-react";

import {
  type AnimatedNavIconHandle,
  ShieldCheckIcon,
} from "@/components/animated-icons";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { HoverTip } from "@/components/ui/tooltip";

import {
  usePermissionMode,
  type PermissionMode,
} from "../composer/controls/use-permission-mode";
import {
  CHECK_SLOT,
  CHECK_SLOT_PAD,
  GROUP_LABEL,
  MENU_PANEL,
  SHORTCUT,
  itemCls,
} from "./menu-styles";

// 权限档危险度配色：绿=安全、橙=需留意、红=危险。用柔和的浅色调
// （比原始 --success/--warning/--danger 淡一档），只用在顶栏芯片
// 选中态的文字/图标/边框色，所以选完能一眼看出当前档；菜单行按
// Claude 选择菜单语法保持素色。
const PERM_COLOR: Record<PermissionMode, string> = {
  ask: "var(--success-soft, #57b47a)",
  plan: "var(--success-soft, #57b47a)",
  acceptEdits: "var(--warning-soft, #e0a54a)",
  auto: "var(--warning-soft, #e0a54a)",
  bypass: "var(--danger-soft, #e05b52)",
};

export function PermissionBadge() {
  const { text } = useTranslation();
  const { mode, options, set } = usePermissionMode();
  const [open, setOpen] = useState(false);
  const [bypassConfirm, setBypassConfirm] = useState(false);
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
      (
        window as unknown as { _closeAllPopovers?: () => void }
      )._closeAllPopovers?.();
    }
    setOpen(next);
  }

  const current = options.find((o) => o.value === mode);
  const label = current?.label ?? mode;

  function pick(value: PermissionMode) {
    // bypass 高危档：不直接切，先弹确认框。其余档直接切、关菜单。
    if (value === "bypass" && mode !== "bypass") {
      setBypassConfirm(true);
      return;
    }
    set(value);
    setOpen(false);
  }

  return (
    <>
      <Popover open={open} onOpenChange={onOpenChange}>
        <HoverTip label={text("Permission mode", "权限模式")}>
          <PopoverTrigger asChild>
            <span
              id="permissionBadge"
              className="runtime-badge permission-badge"
              // 芯片文字/图标/边框跟着当前档的柔和色（图标用 currentColor
              // 继承），选完档一眼可辨；hover/open 态的加深由 CSS 处理。
              style={{
                color: PERM_COLOR[mode],
                borderColor: `color-mix(in srgb, ${PERM_COLOR[mode]} 45%, transparent)`,
              }}
              onMouseEnter={() => iconRef.current?.startAnimation?.()}
              onMouseLeave={() => iconRef.current?.stopAnimation?.()}
            >
              <ShieldCheckIcon
                ref={iconRef}
                size={14}
                className="shrink-0 mr-[4px]"
                aria-hidden="true"
              />
              <span className="badge-details">{label}</span>
            </span>
          </PopoverTrigger>
        </HoverTip>
        <PopoverContent
          align="start"
          side="top"
          /* 10 = 控件行到输入框的 band gap：弹层底缘正好压到输入框底缘
             （底部一排弹层统一对齐输入框，用户定的规则）。 */
          sideOffset={10}
          onOpenAutoFocus={(e) => e.preventDefault()}
          className="w-auto border-0 bg-transparent p-0 shadow-none"
        >
          <div className={`${MENU_PANEL} min-w-[220px]`}>
            <div className={GROUP_LABEL}>{text("Mode", "模式")}</div>
            {options.map((o) => (
              <div
                key={o.value}
                // 选中不铺底色（hover 是唯一底色），选中态只靠右侧勾。
                className={itemCls(false)}
                onClick={() => pick(o.value)}
              >
                <span className="flex-1 truncate">{o.label}</span>
                {/* 数字快捷键右对齐、弱化，排在勾之前。 */}
                {o.key ? <span className={SHORTCUT}>{o.key}</span> : null}
                {/* 右侧：仅当前选中档显示勾；未选中留同宽占位保持对齐。 */}
                {o.value === mode ? (
                  <Check size={14} className={CHECK_SLOT} aria-hidden="true" />
                ) : (
                  <span className={CHECK_SLOT_PAD} />
                )}
              </div>
            ))}
          </div>
        </PopoverContent>
      </Popover>

      {bypassConfirm && typeof document !== "undefined"
        ? createPortal(
            <div
              onClick={() => setBypassConfirm(false)}
              style={{
                position: "fixed",
                inset: 0,
                zIndex: 300,
                background: "rgba(0,0,0,0.45)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <div
                onClick={(e) => e.stopPropagation()}
                style={{
                  width: "min(440px, 92vw)",
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  borderRadius: 14,
                  padding: "20px 24px",
                  boxShadow: "var(--shadow-popover)",
                }}
              >
                <div
                  style={{
                    fontSize: 15,
                    fontWeight: 600,
                    color: "var(--danger, #d72518)",
                    marginBottom: 8,
                  }}
                >
                  {text("Enable Bypass Permissions?", "启用绕过权限？")}
                </div>
                <div
                  style={{
                    fontSize: 13,
                    color: "var(--text-muted)",
                    marginBottom: 20,
                  }}
                >
                  {text(
                    "Every tool runs without asking — including commands that can change or delete files. Only use this in a sandbox / disposable environment.",
                    "所有工具都直接执行、不再询问——包括可改动或删除文件的命令。仅在沙箱 / 一次性环境里使用。",
                  )}
                </div>
                <div
                  style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}
                >
                  <button
                    type="button"
                    onClick={() => setBypassConfirm(false)}
                    style={{
                      padding: "6px 14px",
                      borderRadius: 8,
                      border: "1px solid var(--border)",
                      background: "none",
                      color: "var(--text-primary)",
                      cursor: "pointer",
                    }}
                  >
                    {text("Cancel", "取消")}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      set("bypass");
                      setBypassConfirm(false);
                      setOpen(false);
                    }}
                    style={{
                      padding: "6px 14px",
                      borderRadius: 8,
                      border: "none",
                      background: "var(--danger, #d72518)",
                      color: "#fff",
                      cursor: "pointer",
                    }}
                  >
                    {text("Enable", "启用")}
                  </button>
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
