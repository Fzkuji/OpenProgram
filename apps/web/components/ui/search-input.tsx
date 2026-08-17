import * as React from "react"
import { Search, X } from "lucide-react"

import { cn } from "@/lib/utils"

interface SearchInputProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
  autoFocus?: boolean
  onKeyDown?: React.KeyboardEventHandler<HTMLInputElement>
  "aria-label"?: string
}

/**
 * The one search box for the whole app. Height/radius come from the shared
 * button size tokens; focus lightens the border to `--accent-blue` — the same
 * quiet focus every text input uses (see ui/input.tsx). Clear button and icon
 * stay out of the tab order.
 */
const SearchInput = React.forwardRef<HTMLInputElement, SearchInputProps>(
  ({ value, onChange, placeholder, className, autoFocus, onKeyDown, ...rest }, ref) => {
    const innerRef = React.useRef<HTMLInputElement>(null)
    React.useImperativeHandle(ref, () => innerRef.current as HTMLInputElement)

    return (
      <div
        className={cn(
          "flex items-center gap-1.5 h-[var(--ui-button-h)] rounded-[var(--ui-button-radius)] px-2",
          "bg-[var(--bg-input)] border border-[var(--border)] transition-colors",
          "focus-within:border-[color:var(--accent-blue)]",
          className
        )}
      >
        <Search size={14} className="shrink-0 text-[var(--text-dim)]" aria-hidden="true" />
        <input
          ref={innerRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          autoFocus={autoFocus}
          onKeyDown={onKeyDown}
          spellCheck={false}
          className="search-input-field flex-1 min-w-0 bg-transparent border-0 outline-none text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-dim)]"
          {...rest}
        />
        {value && (
          <button
            type="button"
            tabIndex={-1}
            onClick={() => {
              onChange("")
              innerRef.current?.focus()
            }}
            className="shrink-0 text-[var(--text-dim)] hover:text-[var(--text-primary)] transition-colors"
            aria-label="Clear"
          >
            <X size={14} />
          </button>
        )}
      </div>
    )
  }
)
SearchInput.displayName = "SearchInput"

export { SearchInput }
