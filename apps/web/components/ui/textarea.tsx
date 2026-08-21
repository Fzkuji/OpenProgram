import * as React from "react"

import { cn } from "@/lib/utils"

const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.ComponentProps<"textarea">
>(({ className, ...props }, ref) => {
  return (
    <textarea
      className={cn(
        // Same 1px chrome as Input — no ring, no second frame.
        "ui-text-input flex min-h-[80px] w-full rounded-[var(--ui-button-radius)] border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-base transition-colors placeholder:text-muted-foreground focus:outline-none focus:border-[color:var(--accent-blue)] disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
        className
      )}
      ref={ref}
      {...props}
    />
  )
})
Textarea.displayName = "Textarea"

export { Textarea }
