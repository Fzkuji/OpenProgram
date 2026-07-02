"use client";

/**
 * 二级子菜单——从一个触发行向右弹出，贴着父菜单右边。
 *
 * 用 fixed 定位 + JS 测量（父菜单是 portal/fixed，纯 CSS left:100% 会算错、
 * 且不会翻转、中间还有真空区）。它：
 *   - 紧贴触发行右边缘（gap 很小），左侧留一条透明"桥"覆盖 gap，鼠标从主项
 *     移到子面板不会掉进真空区而误触发关闭；
 *   - 顶部默认对齐触发行；若往下会超出视口底部，则改成底部对齐（向上开）；
 *   - hover 进/出用短延迟（120ms）交给父组件管，这里只管定位 + 存活。
 */
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

const GAP = 4;   // 触发行右边缘到子面板的间隙（小，配合下面的桥）

export function SubMenu({
  open,
  anchorRef,
  onMouseEnter,
  onMouseLeave,
  className,
  minWidth,
  children,
}: {
  open: boolean;
  anchorRef: React.RefObject<HTMLElement | null>;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
  className?: string;
  minWidth?: number;
  children: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number; bridge: number; maxH: number } | null>(null);
  const [panelH, setPanelH] = useState(0);

  // 面板挂载后测高（首帧未知），拿到真高再重算定位（决定向下还是向上）。
  useLayoutEffect(() => {
    const panel = panelRef.current;
    if (!open || !panel) return;
    const ro = new ResizeObserver(() => setPanelH(panel.offsetHeight));
    ro.observe(panel);
    setPanelH(panel.offsetHeight);
    return () => ro.disconnect();
  }, [open]);

  useLayoutEffect(() => {
    if (!open) { setPos(null); return; }
    const anchor = anchorRef.current;
    if (!anchor) return;
    const a = anchor.getBoundingClientRect();
    const left = a.right + GAP;
    const bridge = a.right;
    const vh = window.innerHeight;
    const h = panelH || 0;
    // 优先顶对齐触发行、向下长；向下放不下就底对齐触发行底、向上长。
    // 两种都贴着触发行的一条边，不会飘。仍放不下（面板比视口还高）→ 限高滚动。
    let top: number;
    if (a.top + h <= vh - 8) {
      top = a.top;                       // 向下够放：顶对齐触发行
    } else if (a.bottom - h >= 8) {
      top = a.bottom - h;                // 向上够放：底对齐触发行底
    } else {
      top = 8;                           // 都放不下：贴顶 + 下面 maxH 滚动
    }
    const maxH = vh - top - 8;
    setPos({ left, top, bridge, maxH });
  }, [open, anchorRef, panelH]);

  if (!open || typeof document === "undefined") return null;

  // pos 未算出时先隐藏渲染（让 panelRef 能测高），拿到坐标再显示。
  const ready = pos !== null;
  return createPortal(
    <div
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      style={{
        position: "fixed",
        left: ready ? pos.left : -9999,
        top: ready ? pos.top : 0,
        zIndex: 120,
        visibility: ready ? "visible" : "hidden",
      }}
    >
      {ready && (
        <div
          style={{
            position: "fixed", left: pos.bridge, top: pos.top - 6,
            width: pos.left - pos.bridge + 2, height: 240, zIndex: 119,
          }}
        />
      )}
      <div
        ref={panelRef}
        className={className}
        style={{
          minWidth,
          whiteSpace: "nowrap",
          maxHeight: ready ? pos.maxH : undefined,
          overflowY: "auto",
        }}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
