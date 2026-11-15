"use client";

/**
 * The parts of one menu row, shared by every action menu: the ⋯ and
 * right-click menus in the app and the desktop overlay menus that cover
 * native web views. A row is
 *
 *   [Solar icon]  label                [key badge | check | ›]
 *
 * The icon is a Solar Bold Duotone glyph, the family of the folder icons.
 * The trailing column holds a key price tag (MenuKey) for a key that works
 * while the menu is open or a system shortcut, the row's check when it is
 * checkable, and the chevron when it opens a submenu. The rows themselves
 * are each menu's radix item with itemCls (menu-styles.ts).
 */

import { ChevronRight } from "lucide-react";

import { SolarIcon } from "@/components/solar-icons";
import type { SolarIconName } from "@/components/solar-icons/bodies";
import { CHECK_SLOT_PAD, MENU_KEY, MENU_KEY_PLATE, MENU_KEY_TIP } from "@/components/ui/menu/menu-styles";
import { Kbd } from "@/components/ui/kbd";
import { MenuCheck } from "@/components/ui/menu/menu-check";
import { isMacPlatform } from "@/lib/shortcut-keys";
import { cn } from "@/lib/utils";

/** A key a row answers to. `mod` is ⌘ on macOS and Ctrl elsewhere; a key
 *  with no modifier is a menu-local letter (it works while the menu is
 *  open). */
export interface MenuKeys {
  key: string;
  mod?: boolean;
  shift?: boolean;
  alt?: boolean;
}

export type MenuGlyph = SolarIconName;

export { isMacPlatform };

/** The key as the system writes it: ⌥⇧⌘T on macOS, Ctrl+Alt+Shift+T
 *  elsewhere. */
export function formatMenuKeys(keys: MenuKeys, mac = isMacPlatform()): string {
  const key = keys.key.length === 1 ? keys.key.toUpperCase() : keys.key;
  if (mac) return `${keys.alt ? "⌥" : ""}${keys.shift ? "⇧" : ""}${keys.mod ? "⌘" : ""}${key}`;
  return [keys.mod && "Ctrl", keys.alt && "Alt", keys.shift && "Shift", key].filter(Boolean).join("+");
}

/** The right end of Solar's tag-horizontal outline (bold), cut where its
 *  straight top and bottom edges begin. */
const TAG_TIP_PATH =
  "M12.858 20H10.221C6.3456 20 4.40789 20 3.20394 18.8284C2 17.6569 2 15.7712 2 12C2 8.22876 2 6.34315 3.20394 5.17157C4.40789 4 6.34561 4 10.221 4H12.858C15.0854 4 16.1992 4 17.1289 4.50143C18.0586 5.00286 18.6488 5.92191 19.8294 7.76001L20.5102 8.82001C21.5034 10.3664 22 11.1396 22 12C22 12.8604 21.5034 13.6336 20.5102 15.18L19.8294 16.24C18.6488 18.0781 18.0586 18.9971 17.1289 19.4986C16.1992 20 15.0854 20 12.858 20Z";

/** A key as a price tag: the rounded Solar tip, then the shadcn Kbd. */
export function MenuKey({ keys }: { keys: MenuKeys }) {
  return (
    <span className={MENU_KEY}>
      <svg viewBox="12.858 4 9.142 16" className={MENU_KEY_TIP} aria-hidden="true"><path d={TAG_TIP_PATH} /></svg>
      <Kbd className={MENU_KEY_PLATE}>{formatMenuKeys(keys)}</Kbd>
    </span>
  );
}

export function MenuIcon({ glyph, className }: { glyph: MenuGlyph; className?: string }) {
  return <SolarIcon name={glyph} size={16} motionPreset="none" className={cn("menu-row-icon shrink-0 text-text-muted", className)} aria-hidden="true" />;
}

/** The inside of a row. `checked` undefined: not checkable; false keeps
 *  the check column so labels stay aligned. */
export function MenuRow({ glyph, icon, label, keys, checked, submenu, danger, iconClassName }: {
  glyph?: MenuGlyph;
  /** A non-Solar leading mark (a favicon, a coloured dot) in the icon slot. */
  icon?: React.ReactNode;
  label: React.ReactNode;
  keys?: MenuKeys;
  checked?: boolean;
  submenu?: boolean;
  /** A destructive row: its icon turns red with its label. */
  danger?: boolean;
  iconClassName?: string;
}) {
  return (
    <>
      {glyph ? <MenuIcon glyph={glyph} className={cn(danger && "text-[var(--accent-red)]", iconClassName)} /> : icon ? <span className="inline-flex size-4 shrink-0 items-center justify-center">{icon}</span> : null}
      <span className="min-w-0 flex-1 truncate text-left">{label}</span>
      {keys ? <MenuKey keys={keys} /> : null}
      {checked === undefined ? null : checked ? <MenuCheck /> : <span className={CHECK_SLOT_PAD} aria-hidden="true" />}
      {submenu ? <ChevronRight className="menu-row-chevron size-4 shrink-0 text-text-muted" aria-hidden="true" /> : null}
    </>
  );
}
