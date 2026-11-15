import { MenuCheck } from "@/components/ui/menu/menu-check";
import { CHECK_SLOT_PAD } from "@/components/ui/menu/menu-styles";
import { MenuIcon, type MenuGlyph } from "@/components/ui/menu/menu-parts";

/** A two-line menu row (title over a description) in Web and desktop
 *  overlay menus: the row's Solar icon, then the text, then the check at
 *  the end as on every other menu row. */
export function MenuOptionContent({ label, description, checked, glyph }: {
  label: string;
  description?: string;
  checked?: boolean;
  glyph?: MenuGlyph;
}) {
  // Same type as every two-line menu row (ui/menu/menu-styles.ts):
  // 13/19 title, 12/15 muted description; the icon and the check sit on
  // the title line. pb 1.5px: with the row's 2.5px that is the two-line
  // row's 4px bottom.
  return <span className={`flex w-full min-w-0 items-start gap-[6px] ${description ? "pb-[1.5px]" : ""}`}>
    {glyph ? <span className="mt-[1.5px] inline-flex shrink-0"><MenuIcon glyph={glyph} /></span> : null}
    <span className="min-w-0 flex-1 text-left">
      <span className="block whitespace-normal text-fs-sm leading-[19px]">{label}</span>
      {description ? <span className="block whitespace-normal text-fs-xs leading-[15px] text-text-muted">{description}</span> : null}
    </span>
    {typeof checked === "boolean" ? <span className="mt-[1.5px] inline-flex shrink-0" aria-hidden="true">
      {checked ? <MenuCheck /> : <span className={CHECK_SLOT_PAD} />}
    </span> : null}
  </span>;
}
