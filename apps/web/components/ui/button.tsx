// shadcn/ui Button, radix-nova style (the style ui.shadcn.com's docs
// use), copied verbatim from the official registry
// (/r/styles/radix-nova/button.json). Adaptations: imports point at the
// packages this app already ships (@radix-ui/react-slot, @/lib/utils cn),
// the component forwards its ref because this app is on React 18 (refs
// are not props until 19), and the looks are the app's own: every Button
// has one shape, 8px corners, no border, lifted by the per-theme composer
// shadow pair (shadow-raised / shadow-raised-hover); colour alone marks
// the role. `elevated` is the neutral face for every action that is not
// the main one, and a pressed / checked / on Button wears an accent ring.
// Nova's bordered `outline` and flat `secondary` are not offered.
// Sizes are pinned in px through the --ui-button-h* tokens (styles/design/controls.css):
// xs 24px (row / card / message actions), sm 28px (menus, toolbars,
// chips), default 30px (the sidebar chat row), lg 32px (the composer's
// corner buttons); docs/reference/design/ui/visual-system/surface-system.html lists
// which component takes which. Nova's rem
// sizes rendered at 7/8 on the 14px root and full size inside the old
// docs-scale containers, so one size looked different on different pages. Pick a size at the
// call site through `size`, never with a height class.
import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import * as Slot from "@radix-ui/react-slot"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "group/button inline-flex shrink-0 items-center justify-center rounded-md border border-transparent text-fs-base font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 active:not-aria-[haspopup]:translate-y-px disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-[16px]",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow-raised hover:bg-primary/90 hover:shadow-raised-hover",
        elevated:
          "bg-bg-input text-foreground shadow-raised hover:shadow-raised-hover aria-expanded:shadow-raised-hover aria-pressed:ring-[1.5px] aria-pressed:ring-primary aria-checked:ring-[1.5px] aria-checked:ring-primary data-[state=on]:ring-[1.5px] data-[state=on]:ring-primary",
        ghost:
          "hover:bg-muted hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground dark:hover:bg-muted/50",
        destructive:
          "bg-destructive/10 text-destructive shadow-raised hover:bg-destructive/20 hover:shadow-raised-hover focus-visible:border-destructive/40 focus-visible:ring-destructive/20 dark:bg-destructive/20 dark:hover:bg-destructive/30 dark:focus-visible:ring-destructive/40",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default:
          "h-(--ui-button-h) gap-[6px] px-[10px] has-data-[icon=inline-end]:pr-[8px] has-data-[icon=inline-start]:pl-[8px]",
        xs: "h-(--ui-button-h-xs) gap-[4px] px-[8px] text-fs-xs has-data-[icon=inline-end]:pr-[6px] has-data-[icon=inline-start]:pl-[6px] [&_svg:not([class*='size-'])]:size-[12px]",
        sm: "h-(--ui-button-h-sm) gap-[4px] px-[10px] text-fs-sm has-data-[icon=inline-end]:pr-[6px] has-data-[icon=inline-start]:pl-[6px] [&_svg:not([class*='size-'])]:size-[14px]",
        lg: "h-(--ui-button-h-lg) gap-[6px] px-[10px] has-data-[icon=inline-end]:pr-[8px] has-data-[icon=inline-start]:pl-[8px]",
        icon: "size-(--ui-button-h)",
        "icon-xs": "size-(--ui-button-h-xs) [&_svg:not([class*='size-'])]:size-[12px]",
        "icon-sm": "size-(--ui-button-h-sm) [&_svg:not([class*='size-'])]:size-[14px]",
        "icon-lg": "size-(--ui-button-h-lg)",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

const Button = React.forwardRef<
  HTMLButtonElement,
  React.ComponentProps<"button"> &
    VariantProps<typeof buttonVariants> & {
      asChild?: boolean
    }
>(function Button(
  { className, variant = "default", size = "default", asChild = false, ...props },
  ref
) {
  const Comp = asChild ? Slot.Root : "button"

  return (
    <Comp
      ref={ref}
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
})

export { Button, buttonVariants }
