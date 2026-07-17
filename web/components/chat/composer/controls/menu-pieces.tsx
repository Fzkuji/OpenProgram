/**
 * Small visual pieces used by the Composer's bottom row — active-tool
 * chip + plus-menu row. Behaviour stays in Composer; these are pure
 * presentation.
 */
"use client";

import {
  cloneElement,
  forwardRef,
  isValidElement,
  useLayoutEffect,
  useRef,
  type HTMLAttributes,
  type ReactElement,
  type ReactNode,
} from "react";

import styles from "../composer.module.css";
import {
  CheckIcon,
  XIcon,
  type AnimatedNavIconHandle,
} from "@/components/animated-icons";
import { useTranslation } from "@/lib/i18n";

/**
 * Drive an animated toolbar icon from its *container's* hover, so the
 * whole row / chip is the hover target (claude.ai-style) — not just the
 * small glyph. Clones the passed icon element with a ref to its
 * animation handle; the returned ``onMouseEnter/Leave`` start/stop it.
 *
 * Animated icons flip to "controlled" mode once a ref is attached, so
 * they no longer self-animate on their own hover — the container is the
 * single driver. A non-animated icon (e.g. the 📎 emoji span) gets the
 * ref on a DOM node with no ``startAnimation``; the optional call simply
 * no-ops, so this is safe for any icon.
 */
function useHoverDrivenIcon(icon: ReactNode) {
  const ref = useRef<AnimatedNavIconHandle>(null);
  const node = isValidElement(icon)
    ? cloneElement(icon as ReactElement, { ref } as Record<string, unknown>)
    : icon;
  return {
    node,
    onMouseEnter: () => ref.current?.startAnimation?.(),
    onMouseLeave: () => ref.current?.stopAnimation?.(),
  };
}

/** forwardRef + spread props so it can be a <HoverTip> trigger child
 * (radix Slot passes ref + pointer/focus handlers through). The tooltip
 * is the HoverTip, NOT a CSS ::after — the chip has `overflow: hidden`
 * for its round clip, which would crop an ::after bubble. */
type ToolChipProps = {
  icon: ReactNode;
  label: string;
  /** Whether the tool is enabled. Off → muted, no × (click turns it on). */
  on?: boolean;
  onToggle: () => void;
} & HTMLAttributes<HTMLDivElement>;

export const ToolChip = forwardRef<HTMLDivElement, ToolChipProps>(function ToolChip(
  { icon, label, on = true, onToggle, ...rest },
  ref,
) {
  const { text } = useTranslation();
  const { node, onMouseEnter, onMouseLeave } = useHoverDrivenIcon(icon);
  // The × that slides out on hover is the animated XIcon, driven from
  // the same chip hover as the main glyph.
  const closeRef = useRef<AnimatedNavIconHandle>(null);
  return (
    <div
      ref={ref}
      {...rest}
      className={`${styles.toolChip} ${on ? "" : styles.toolChipOff}`}
      onClick={onToggle}
      onMouseEnter={() => {
        onMouseEnter();
        closeRef.current?.startAnimation?.();
      }}
      onMouseLeave={() => {
        onMouseLeave();
        closeRef.current?.stopAnimation?.();
      }}
      aria-label={label}
    >
      <span className={styles.toolChipIcon}>{node}</span>
      {on && (
        <span className={styles.toolChipClose} aria-label={text("Turn off", "关闭")}>
          <XIcon ref={closeRef} size={12} />
        </span>
      )}
    </div>
  );
});

export function PlusMenuItem({
  active,
  onClick,
  icon,
  label,
  title,
  trailing,
}: {
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
  label: string;
  title?: string;
  trailing?: ReactNode;   // 右侧附加（未勾选时显示，如数字快捷键 / "Enable"）
}) {
  const { node, onMouseEnter, onMouseLeave } = useHoverDrivenIcon(icon);
  // The ✓ plays its draw-in animation exactly once — at the moment the
  // item becomes checked (active: false → true). It does NOT animate on
  // hover: attaching a ref puts the CheckIcon in "controlled" mode, so it
  // no longer self-animates on its own hover, and we never drive it from
  // the row's mouse handlers. Re-opening the menu on an already-checked
  // item does not replay it (prevActive starts equal to active on mount,
  // so the false→true edge isn't seen). useLayoutEffect fires before
  // paint, so the path starts hidden instead of flashing fully-drawn.
  const checkRef = useRef<AnimatedNavIconHandle>(null);
  const prevActive = useRef(active);
  useLayoutEffect(() => {
    if (active && !prevActive.current) {
      checkRef.current?.startAnimation?.();
    }
    prevActive.current = active;
  }, [active]);
  return (
    <div
      className={`${styles.plusMenuItem} ${active ? styles.active : ""}`}
      onClick={onClick}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      title={title}
    >
      <div className={styles.plusMenuLeft}>
        {icon != null && <span className={styles.plusMenuIcon}>{node}</span>}
        <span className={styles.plusMenuLabel}>{label}</span>
      </div>
      <div className={styles.plusMenuRight}>
        {/* 文法 A：勾 = 14px ink；trailing（子菜单箭头/提示）常驻，
            不再被勾顶掉——Tools 行开着时也要能看见 › 子菜单提示。 */}
        {active ? (
          <CheckIcon
            ref={checkRef}
            size={14}
            style={{ color: "var(--text-bright)" }}
          />
        ) : null}
        {trailing != null ? (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{trailing}</span>
        ) : null}
      </div>
    </div>
  );
}
