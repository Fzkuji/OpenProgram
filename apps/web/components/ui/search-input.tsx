"use client"

import * as React from "react"

import { SearchIcon, XIcon } from "@/components/animated-icons"
import type { AnimatedNavIconHandle } from "@/components/animated-icons"

import { cn } from "@/lib/utils"
import { useTranslation } from "@/lib/i18n"

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
    const searchRef = React.useRef<AnimatedNavIconHandle>(null)
    const clearRef = React.useRef<AnimatedNavIconHandle>(null)
    const { text } = useTranslation()
    React.useImperativeHandle(ref, () => innerRef.current as HTMLInputElement)

    return (
      <div
        className={cn(
          "flex items-center gap-1.5 h-[var(--ui-button-h)] rounded-md px-2",
          "bg-[var(--bg-input)] border border-[var(--border)] transition-colors",
          "focus-within:border-[color:var(--accent-blue)]",
          className
        )}
        onMouseEnter={() => searchRef.current?.startAnimation?.()}
        onMouseLeave={() => searchRef.current?.stopAnimation?.()}
      >
        <SearchIcon size={14} className="shrink-0 text-text-secondary" ref={searchRef} aria-hidden="true" />
        <input
          ref={innerRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          autoFocus={autoFocus}
          onKeyDown={onKeyDown}
          spellCheck={false}
          className="search-input-field flex-1 min-w-0 bg-transparent border-0 outline-none text-fs-sm text-text-primary placeholder:text-text-secondary"
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
            className="shrink-0 text-text-secondary hover:text-text-primary transition-colors"
            aria-label={text("Clear search", "清除搜索")}
            onMouseEnter={() => clearRef.current?.startAnimation?.()}
            onMouseLeave={() => clearRef.current?.stopAnimation?.()}
          >
            <XIcon size={14} ref={clearRef} />
          </button>
        )}
      </div>
    )
  }
)
SearchInput.displayName = "SearchInput"

export { SearchInput }
