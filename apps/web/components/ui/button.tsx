import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

// Buttons live on the panel surface and use the ``button`` size set
// from docs/design/ui/surface-system.md: ONE height (--ui-button-h,
// 36px), ONE radius (--ui-button-radius, 10px). No sm / lg ladder —
// every Button rendered anywhere is the same height + same corners.
// (The previous default/sm/lg/icon ladder is gone; ``size="icon"``
// is the only modifier kept because square buttons still need a
// width that matches the height.)
const buttonVariants = cva(
  // leading-none drops the inherited 1.25 line-height so the text
  // line-box equals the font size; flex ``items-center`` then puts
  // the glyph centre exactly on the button centre. Without this
  // the extra line-leading (~3px) shows up as a tiny vertical
  // drift between glyphs and pill geometry on some fonts.
  "inline-flex h-[var(--ui-button-h)] rounded-[var(--ui-button-radius)] items-center justify-center gap-2 whitespace-nowrap px-4 text-sm leading-none font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        // A button reads as a button at rest: a subtle raised surface
        // (one layer lighter than the panel it sits on) with brand-
        // coloured text. Hover only LIFTS the surface one more step —
        // the text colour never flips. The old design filled the whole
        // button with the brand colour on hover and inverted the text to
        // black; that full inversion was too loud in dense rows, so it's
        // gone. No border — the surface lift alone separates the button
        // from the panel. See docs/design/ui/surface-system.md.
        default:
          "bg-secondary text-primary hover:bg-accent",
        // Same shape for destructive: raised surface + red text, surface
        // lifts on hover, text stays red (no fill, no inversion).
        destructive:
          "bg-secondary text-destructive hover:bg-accent",
        outline:
          "bg-background hover:bg-accent hover:text-accent-foreground",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        // Default = the canonical button size (token-driven height +
        // radius set on the base class). ``icon`` pins width to the
        // same token for a square footprint. ``sm`` / ``lg`` /
        // ``icon-sm`` are kept ONLY as aliases for backwards
        // compatibility with the 33 + 5 + ... existing callers —
        // they now resolve to the same size, on purpose.
        default: "",
        sm: "",
        lg: "",
        icon: "w-[var(--ui-button-h)] px-0",
        "icon-sm": "w-[var(--ui-button-h)] px-0",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = "Button"

export { Button, buttonVariants }
