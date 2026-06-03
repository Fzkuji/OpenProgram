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
  useRef,
  type HTMLAttributes,
  type ReactElement,
  type ReactNode,
} from "react";

import styles from "../composer.module.css";
import { CheckIcon, ChipCloseIcon } from "../icons";
import type { AnimatedNavIconHandle } from "@/components/animated-icons";
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
  return (
    <div
      ref={ref}
      {...rest}
      className={`${styles.toolChip} ${on ? "" : styles.toolChipOff}`}
      onClick={onToggle}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      aria-label={label}
    >
      <span className={styles.toolChipIcon}>{node}</span>
      {on && (
        <span className={styles.toolChipClose} aria-label={text("Turn off", "关闭")}>
          <ChipCloseIcon />
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
}: {
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
  label: string;
  title?: string;
}) {
  const { node, onMouseEnter, onMouseLeave } = useHoverDrivenIcon(icon);
  return (
    <div
      className={`${styles.plusMenuItem} ${active ? styles.active : ""}`}
      onClick={onClick}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      title={title}
    >
      <div className={styles.plusMenuLeft}>
        <span className={styles.plusMenuIcon}>{node}</span>
        <span className={styles.plusMenuLabel}>{label}</span>
      </div>
      <div className={styles.plusMenuRight}>{active && <CheckIcon />}</div>
    </div>
  );
}
