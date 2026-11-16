"use client"

// An Input for a secret (an API key, a token): masked, in the mono face, with
// an icon button at its end that shows or hides what was typed. While it is
// masked, a value of eight characters or more shows its last four before the
// button, so a pasted key can be told from another. The value stays in the
// field; nothing here reads a stored secret back.
import * as React from "react"
import { EyeIcon, EyeOffIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useTranslation } from "@/lib/i18n"
import { cn } from "@/lib/utils"

const SecretInput = React.forwardRef<
  HTMLInputElement,
  Omit<React.ComponentProps<"input">, "type">
>(({ className, ...props }, ref) => {
  const { text } = useTranslation()
  const [shown, setShown] = React.useState(false)
  const value = typeof props.value === "string" ? props.value : ""
  const tail = !shown && value.length >= 8 ? value.slice(-4) : ""
  return (
    <div data-slot="secret-input" className="relative w-full min-w-0">
      <Input
        ref={ref}
        type={shown ? "text" : "password"}
        autoComplete="off"
        spellCheck={false}
        className={cn(tail ? "pr-[calc(var(--ui-button-h)+5ch)]" : "pr-(--ui-button-h)", "font-mono tracking-[0.02em]", className)}
        {...props}
      />
      {tail ? (
        <span data-slot="secret-input-tail" aria-hidden="true"
          className="pointer-events-none absolute top-1/2 right-[calc(var(--ui-button-h)+2px)] -translate-y-1/2 font-mono text-fs-xs text-text-muted">
          {tail}
        </span>
      ) : null}
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        className="absolute top-1/2 right-[3px] -translate-y-1/2 text-text-muted"
        aria-label={shown ? text("Hide", "隐藏") : text("Show", "显示")}
        aria-pressed={shown}
        onClick={() => setShown((value) => !value)}
      >
        {shown ? <EyeOffIcon /> : <EyeIcon />}
      </Button>
    </div>
  )
})
SecretInput.displayName = "SecretInput"

export { SecretInput }
