"use client"

// A Button that copies a value: a ghost icon button with the copy glyph that
// turns to a check for a moment once the value is on the clipboard. The hint
// rides the button (HoverTip) and names what is copied. Copying goes through
// lib/clipboard (the desktop bridge first, then the browser's clipboard).
import * as React from "react"
import { CheckIcon, CopyIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { HoverTip } from "@/components/ui/tooltip"
import { useTranslation } from "@/lib/i18n"
import { copyText } from "@/lib/clipboard"

function CopyButton({
  value,
  label,
  className,
  size = "icon-xs",
}: {
  value: string
  /** What is copied, for the hint and the accessible name ("Copy command"). */
  label?: string
  className?: string
  size?: "icon-xs" | "icon-sm"
}) {
  const { text } = useTranslation()
  const [copied, setCopied] = React.useState(false)
  React.useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 1500)
    return () => window.clearTimeout(timer)
  }, [copied])
  const name = copied ? text("Copied", "已复制") : label ?? text("Copy", "复制")
  return (
    <HoverTip label={name}>
      <Button
        type="button"
        variant="ghost"
        size={size}
        className={className}
        aria-label={name}
        data-copied={copied || undefined}
        onClick={() => void copyText(value).then((ok) => setCopied(ok))}
      >
        {copied ? <CheckIcon /> : <CopyIcon />}
      </Button>
    </HoverTip>
  )
}

export { CopyButton }
