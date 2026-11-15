"use client";

import { isMacPlatform } from "@/lib/shortcut-keys";
import { useRef, useState } from "react";
import type { ReactElement, ReactNode, RefObject } from "react";
import { Globe, Lock, Search } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import { HoverTip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { addressDisplayParts, shortcutHint } from "@/lib/browser/browser-layout";
import styles from "../browser.module.css";

/** Toolbar control tip. It opens above the control: the page below is a
 *  native view in the desktop App and would cover a tip drawn there. So it
 *  never flips, and it keeps to one line (a long session title truncates)
 *  so it fits in the room above the toolbar. */
export function ToolbarTip({
  label,
  shortcut,
  children,
}: {
  label: string;
  shortcut?: { key: string; shift?: boolean };
  children: ReactElement;
}) {
  const mac = isMacPlatform();
  return (
    <HoverTip
      side="top"
      avoidCollisions={false}
      label={
        <span className={`${styles.toolbarTip} inline-flex items-center gap-[8px] min-w-0 max-w-[18rem]`}>
          <span className={styles.toolbarTipLabel}>{label}</span>
          {shortcut ? <kbd>{shortcutHint(shortcut.key, { shift: shortcut.shift, mac })}</kbd> : null}
        </span>
      }
    >
      {children}
    </HoverTip>
  );
}

/** The bookmark star inside the address pill: a small ghost icon Button. */
export function addressStarButton() {
  return cn(buttonVariants({ variant: "ghost", size: "icon-xs" }), styles.webAddressStar);
}

/** The browser address field: a borderless pill like the chat input box.
 *  At rest it shows the host brightly and the path dimmed; focused, it is
 *  a plain text field with the full address. */
export function AddressField({
  inputRef,
  value,
  onChange,
  onSubmit,
  placeholder,
  ariaLabel,
  autoFocus,
  children,
}: {
  inputRef?: RefObject<HTMLInputElement>;
  value: string;
  onChange(value: string): void;
  onSubmit(): void;
  placeholder?: string;
  ariaLabel: string;
  autoFocus?: boolean;
  children?: ReactNode;
}) {
  const ownRef = useRef<HTMLInputElement>(null);
  const ref = inputRef ?? ownRef;
  const [focused, setFocused] = useState(false);
  const parts = focused ? null : addressDisplayParts(value);
  const secure = addressDisplayParts(value)?.secure;
  const Icon = !value.trim() ? Search : secure ? Lock : Globe;
  return (
    <div
      className={styles.webAddressBox}
      data-focused={focused || undefined}
      onMouseDown={(event) => {
        // A press on the pill's padding or icon edits the address too.
        if ((event.target as Element).closest("button, input")) return;
        event.preventDefault();
        ref.current?.focus();
      }}
    >
      <Icon size={14} className={styles.webAddressIcon} aria-hidden="true" />
      <span className="relative flex-1 min-w-0 self-stretch flex">
        <input
          ref={ref}
          className={styles.webAddress}
          data-dressed={parts ? "" : undefined}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") onSubmit();
          }}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          placeholder={placeholder}
          spellCheck={false}
          autoComplete="off"
          aria-label={ariaLabel}
          autoFocus={autoFocus}
        />
        {parts ? (
          <span className={styles.webAddressDisplay} aria-hidden="true">
            <span className={styles.webAddressHost}>{parts.host}</span>
            {parts.rest}
          </span>
        ) : null}
      </span>
      {children}
    </div>
  );
}
