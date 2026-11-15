/**
 * Small visual pieces used by the Composer's bottom row — active-tool
 * chip + options-menu row. Behaviour stays in Composer; these are pure
 * presentation. The row is a radix DropdownMenuItem on the shared menu
 * grammar (ui/menu/menu-styles), so the options menu reads exactly like
 * every other popup menu in the app.
 */
"use client";

import {
  cloneElement,
  forwardRef,
  isValidElement,
  useRef,
  type ComponentPropsWithoutRef,
  type ElementRef,
  type HTMLAttributes,
  type ReactElement,
  type ReactNode,
} from "react";

import styles from "../composer.module.css";
import { type AnimatedNavIconHandle } from "@/components/animated-icons";
import { SolarIcon } from "@/components/solar-icons";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { useTranslation } from "@/lib/i18n";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  CHECK_SLOT_PAD,
  MENU_ITEM_STATES,
  itemCls,
} from "@/components/ui/menu/menu-styles";
import { MenuCheck } from "@/components/ui/menu/menu-check";

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
  // The × that slides out on hover pops with the same chip hover as
  // the main glyph.
  const closeRef = useRef<AnimatedNavIconHandle>(null);
  return (
    <div
      ref={ref}
      {...rest}
      className={`${cn(buttonVariants({ variant: "elevated", size: "icon-sm" }))} ${styles.toolChip} ${on ? "" : styles.toolChipOff}`}
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
          <SolarIcon ref={closeRef} name="close-circle" size={12} />
        </span>
      )}
    </div>
  );
});

type PlusMenuRowProps = Omit<
  ComponentPropsWithoutRef<typeof DropdownMenuItem>,
  "onSelect" | "children"
> & {
  /** Checked state — a right-aligned check in the shared CHECK_SLOT column. */
  active: boolean;
  /** 16px leading glyph; null for rows without one (the profile list). */
  icon: ReactNode;
  label: string;
  /** Toggles keep the menu open; plain actions (attach, pick a profile)
   *  let radix close it. */
  keepOpen?: boolean;
  onSelect: () => void;
  /** Right-side metadata, drawn before the check column (grammar A). */
  trailing?: ReactNode;
};

/**
 * One options-menu row: a radix DropdownMenuItem on the canonical
 * `itemCls` (24px · 13px/19px · 6px radius · hover is the only tint) plus the
 * radix keyboard / disabled states. Layout is the shared grammar —
 * `[16px icon] label … trailing [check | pad]` — so labels and checks sit
 * in the same columns as the top-bar menus.
 */
export const PlusMenuRow = forwardRef<ElementRef<typeof DropdownMenuItem>, PlusMenuRowProps>(
  function PlusMenuRow(
    { active, icon, label, keepOpen = false, onSelect, trailing, className, ...rest },
    ref,
  ) {
    const { node, onMouseEnter, onMouseLeave } = useHoverDrivenIcon(icon);
    return (
      <DropdownMenuItem
        ref={ref}
        {...rest}
        className={cn(itemCls(false), MENU_ITEM_STATES, className)}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
        onSelect={(e) => {
          if (keepOpen) e.preventDefault();
          onSelect();
        }}
      >
        {/* Fixed 16px icon column so labels align across rows whatever a
            glyph's intrinsic box. */}
        {icon != null ? (
          <span
            className="flex h-[16px] w-[16px] shrink-0 items-center justify-center"
            aria-hidden="true"
          >
            {node}
          </span>
        ) : null}
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {trailing}
        {/* Grammar A: the selected row shows the blue check; every
            other row reserves the column so right-side metadata (the
            Tools gear) stays aligned. */}
        {active ? (
          <MenuCheck />
        ) : (
          <span className={CHECK_SLOT_PAD} />
        )}
      </DropdownMenuItem>
    );
  },
);
