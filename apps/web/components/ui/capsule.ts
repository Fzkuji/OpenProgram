// The one look of a choice among a few options, for controls that cannot be
// a ToggleGroup or TabsList (tab rows a page builds itself): a rounded
// groove set into the surface, the options plain text, and a raised face
// (<SlidingThumb>) sliding under the chosen one. Two sizes: a view, filter
// or setting choice is as tall as a button; a page's section switch is one
// step larger and sits centred on the line between its header and content.
// Options keep `role="tab"` + `aria-selected`, or `aria-pressed`, which is
// what the thumb and the bright ink follow.

/** The groove of a view, filter or setting choice: 32px, its corner the
 *  option's 8px plus the 3px around it. */
export const CAPSULE_GROOVE =
  "relative isolate inline-flex w-fit shrink-0 items-center rounded-groove bg-groove p-[3px] shadow-groove"

/** One option in CAPSULE_GROOVE. */
export const CAPSULE_OPTION =
  "relative z-10 inline-flex h-(--ui-choice-h) shrink-0 cursor-pointer items-center justify-center gap-1.5 rounded-md border-0 bg-transparent px-3 text-fs-sm font-medium whitespace-nowrap text-text-secondary outline-none transition-colors duration-(--motion-fast) hover:text-text-bright focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-selected:text-text-bright aria-pressed:text-text-bright disabled:pointer-events-none disabled:opacity-50"

/** The groove of a page's section switch: one size up (4px around 8px). */
export const SECTION_GROOVE =
  "relative isolate inline-flex w-fit shrink-0 items-center rounded-xl bg-groove p-1 shadow-groove"

/** One section in SECTION_GROOVE. */
export const SECTION_OPTION =
  "relative z-10 inline-flex h-(--ui-button-h) shrink-0 cursor-pointer items-center justify-center gap-1.5 rounded-md border-0 bg-transparent px-4 text-fs-base font-medium whitespace-nowrap text-text-secondary outline-none transition-colors duration-(--motion-fast) hover:text-text-bright focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-selected:text-text-bright aria-pressed:text-text-bright"

/** The row that carries a page's section switch: the switch centred on the
 *  line between the header above and the content below (the line is this
 *  row's background, so the row can scroll sideways when the window is narrow). */
export const SECTION_BAR =
  "flex shrink-0 justify-center-safe overflow-x-auto px-6 py-px bg-[linear-gradient(to_bottom,transparent_calc(50%-0.5px),var(--border)_calc(50%-0.5px),var(--border)_calc(50%+0.5px),transparent_calc(50%+0.5px))]"

/** What the thumb follows in a groove built from these classes. */
export const CHOSEN_OPTION = '[aria-selected="true"], [aria-pressed="true"]'
