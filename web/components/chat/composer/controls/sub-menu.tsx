"use client";

/**
 * 二级子菜单——从一个触发行向右弹出，贴着父菜单右边。
 *
 * 用 Radix Popover 做定位（仓库已装 @radix-ui/react-popover）：side="right"
 * 贴触发行右侧、align="start" 顶对齐、放不下自动翻转/避让——碰撞检测和时序
 * 都由 Radix 处理，不再手写 getBoundingClientRect（那会因父菜单是 fixed
 * portal 而拿到未 settle 的坐标、飘位）。
 *
 * 触发行本身作为 Popover.Anchor；open 受控（父组件的 hover 逻辑管）。
 */
import { type ReactNode } from "react";
import * as Popover from "@radix-ui/react-popover";

export function SubMenu({
  open,
  anchor,
  onMouseEnter,
  onMouseLeave,
  className,
  minWidth,
  children,
}: {
  open: boolean;
  anchor: ReactNode;            // 触发行（含 PlusMenuItem），作为定位锚点
  onMouseEnter: () => void;
  onMouseLeave: () => void;
  className?: string;
  minWidth?: number;
  children: ReactNode;
}) {
  return (
    <Popover.Root open={open}>
      <Popover.Anchor asChild>
        <div onMouseEnter={onMouseEnter} onMouseLeave={onMouseLeave}>
          {anchor}
        </div>
      </Popover.Anchor>
      <Popover.Portal>
        <Popover.Content
          side="right"
          align="start"
          sideOffset={6}
          collisionPadding={8}
          onOpenAutoFocus={(e) => e.preventDefault()}
          onMouseEnter={onMouseEnter}
          onMouseLeave={onMouseLeave}
          className={className}
          style={{ minWidth, whiteSpace: "nowrap", zIndex: 120 }}
        >
          {children}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
