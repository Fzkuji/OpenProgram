"use client"

// shadcn/ui Select, radix-nova style (the style ui.shadcn.com's docs
// use), copied verbatim from the official registry
// (/r/styles/radix-nova/select.json). Adaptations:
// - Imports point at the packages this app ships (@radix-ui/react-select,
//   lucide-react icons for the registry's IconPlaceholder, @/lib/utils cn),
//   and the parts forward their refs (React 18: refs are not props
//   until 19).
// - The popup stacks above dialogs (z-(--z-popover); DialogContent is z-(--z-dialog)).
// - Sizes are the app's tiers (styles/design/), like every menu panel.
// `SimpleSelect` below is the one-line form for a value + option list,
// which is how the app's former <select> elements read.

import * as React from "react"
import * as SelectPrimitive from "@radix-ui/react-select"
import { CheckIcon, ChevronDownIcon, ChevronUpIcon } from "lucide-react"

import { cn } from "@/lib/utils"

const Select = SelectPrimitive.Root

const SelectGroup = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Group>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Group>
>(({ className, ...props }, ref) => (
  <SelectPrimitive.Group
    ref={ref}
    data-slot="select-group"
    className={cn("scroll-my-1 p-1", className)}
    {...props}
  />
))
SelectGroup.displayName = SelectPrimitive.Group.displayName

const SelectValue = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Value>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Value>
>((props, ref) => <SelectPrimitive.Value ref={ref} data-slot="select-value" {...props} />)
SelectValue.displayName = SelectPrimitive.Value.displayName

const SelectTrigger = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Trigger> & {
    size?: "sm" | "default"
  }
>(({ className, size = "default", children, ...props }, ref) => (
  <SelectPrimitive.Trigger
    ref={ref}
    data-slot="select-trigger"
    data-size={size}
    className={cn(
      "flex w-fit items-center justify-between gap-1.5 rounded-lg border border-input bg-transparent py-2 pr-2 pl-2.5 text-fs-base whitespace-nowrap transition-colors outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 data-placeholder:text-muted-foreground data-[size=default]:h-(--ui-button-h) data-[size=sm]:h-(--ui-button-h-sm) data-[size=sm]:rounded-md *:data-[slot=select-value]:line-clamp-1 *:data-[slot=select-value]:flex *:data-[slot=select-value]:items-center *:data-[slot=select-value]:gap-1.5 dark:bg-input/30 dark:hover:bg-input/50 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
      className
    )}
    {...props}
  >
    {children}
    <SelectPrimitive.Icon asChild>
      <ChevronDownIcon className="pointer-events-none size-4 text-muted-foreground" />
    </SelectPrimitive.Icon>
  </SelectPrimitive.Trigger>
))
SelectTrigger.displayName = SelectPrimitive.Trigger.displayName

const SelectScrollUpButton = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.ScrollUpButton>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.ScrollUpButton>
>(({ className, ...props }, ref) => (
  <SelectPrimitive.ScrollUpButton
    ref={ref}
    data-slot="select-scroll-up-button"
    className={cn(
      "z-10 flex cursor-default items-center justify-center bg-popover py-1 [&_svg:not([class*='size-'])]:size-4",
      className
    )}
    {...props}
  >
    <ChevronUpIcon />
  </SelectPrimitive.ScrollUpButton>
))
SelectScrollUpButton.displayName = SelectPrimitive.ScrollUpButton.displayName

const SelectScrollDownButton = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.ScrollDownButton>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.ScrollDownButton>
>(({ className, ...props }, ref) => (
  <SelectPrimitive.ScrollDownButton
    ref={ref}
    data-slot="select-scroll-down-button"
    className={cn(
      "z-10 flex cursor-default items-center justify-center bg-popover py-1 [&_svg:not([class*='size-'])]:size-4",
      className
    )}
    {...props}
  >
    <ChevronDownIcon />
  </SelectPrimitive.ScrollDownButton>
))
SelectScrollDownButton.displayName = SelectPrimitive.ScrollDownButton.displayName

const SelectContent = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Content>
>(({ className, children, position = "item-aligned", align = "center", ...props }, ref) => (
  <SelectPrimitive.Portal>
    <SelectPrimitive.Content
      ref={ref}
      data-slot="select-content"
      data-align-trigger={position === "item-aligned"}
      // The registry's duration-100 would also speed the hover shadow past
      // every other popup's; only the open/close animation is 100ms.
      className={cn(
        "relative z-(--z-popover) max-h-(--radix-select-content-available-height) min-w-36 origin-(--radix-select-content-transform-origin) overflow-x-hidden overflow-y-auto rounded-lg bg-popover text-popover-foreground shadow-(--popover-shadow) transition-shadow hover:shadow-(--popover-shadow-hover) ring-1 ring-(color:--popover-ring) [--tw-animation-duration:100ms] data-[align-trigger=true]:animate-none data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
        position === "popper" &&
          "data-[side=bottom]:translate-y-1 data-[side=left]:-translate-x-1 data-[side=right]:translate-x-1 data-[side=top]:-translate-y-1",
        className
      )}
      position={position}
      align={align}
      {...props}
    >
      <SelectScrollUpButton />
      <SelectPrimitive.Viewport
        data-position={position}
        className="data-[position=popper]:h-(--radix-select-trigger-height) data-[position=popper]:w-full data-[position=popper]:min-w-(--radix-select-trigger-width)"
      >
        {children}
      </SelectPrimitive.Viewport>
      <SelectScrollDownButton />
    </SelectPrimitive.Content>
  </SelectPrimitive.Portal>
))
SelectContent.displayName = SelectPrimitive.Content.displayName

const SelectLabel = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Label>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Label>
>(({ className, ...props }, ref) => (
  <SelectPrimitive.Label
    ref={ref}
    data-slot="select-label"
    className={cn("px-1.5 py-1 text-fs-xs text-muted-foreground", className)}
    {...props}
  />
))
SelectLabel.displayName = SelectPrimitive.Label.displayName

const SelectItem = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Item>
>(({ className, children, ...props }, ref) => (
  <SelectPrimitive.Item
    ref={ref}
    data-slot="select-item"
    className={cn(
      "relative flex w-full cursor-pointer items-center gap-1.5 rounded-md py-1 pr-8 pl-1.5 text-fs-base outline-hidden select-none focus:bg-accent focus:text-accent-foreground not-data-[variant=destructive]:focus:**:text-accent-foreground data-disabled:pointer-events-none data-disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 *:[span]:last:flex *:[span]:last:items-center *:[span]:last:gap-2",
      className
    )}
    {...props}
  >
    <span className="pointer-events-none absolute right-2 flex size-4 items-center justify-center">
      <SelectPrimitive.ItemIndicator>
        <CheckIcon className="pointer-events-none" />
      </SelectPrimitive.ItemIndicator>
    </span>
    <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
  </SelectPrimitive.Item>
))
SelectItem.displayName = SelectPrimitive.Item.displayName

const SelectSeparator = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Separator>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Separator>
>(({ className, ...props }, ref) => (
  <SelectPrimitive.Separator
    ref={ref}
    data-slot="select-separator"
    className={cn("pointer-events-none -mx-1 my-1 h-px bg-border", className)}
    {...props}
  />
))
SelectSeparator.displayName = SelectPrimitive.Separator.displayName

// Radix reserves "" for "no selection" (the placeholder shows), so an
// option whose value is "" travels under this sentinel and comes back
// out as "". Without such an option, "" is the placeholder state.
const EMPTY = "__empty__"

export interface SimpleSelectOption<T extends string = string> {
  value: T
  label: React.ReactNode
  disabled?: boolean
}

export interface SimpleSelectGroup<T extends string = string> {
  label: React.ReactNode
  options: SimpleSelectOption<T>[]
}

/**
 * A value + option list as one Select, laid out like the docs example:
 * the popup opens with a SelectLabel heading that names what is being
 * chosen (`label`, else `aria-label`), then the options. Grouped options
 * (`{label, options}` entries) each get their own heading instead. The
 * default-size trigger is the docs' field width (w-full max-w-48).
 */
function SimpleSelect<T extends string = string>({
  value,
  onValueChange,
  options,
  label,
  placeholder,
  size = "default",
  disabled,
  className,
  contentClassName,
  id,
  name,
  "aria-label": ariaLabel,
  "aria-describedby": ariaDescribedBy,
}: {
  value: T
  onValueChange: (value: T) => void
  options: (SimpleSelectOption<T> | SimpleSelectGroup<T>)[]
  label?: React.ReactNode
  placeholder?: React.ReactNode
  size?: "sm" | "default"
  disabled?: boolean
  className?: string
  contentClassName?: string
  id?: string
  name?: string
  "aria-label"?: string
  "aria-describedby"?: string
}) {
  const flat = options.flatMap((entry) => ("options" in entry ? entry.options : [entry]))
  const hasEmpty = flat.some((o) => o.value === "")
  const toRadix = (v: string) => (v === "" && hasEmpty ? EMPTY : v)
  const heading = label ?? ariaLabel
  const item = (o: SimpleSelectOption<T>) => (
    <SelectItem key={toRadix(o.value)} value={toRadix(o.value)} disabled={o.disabled}>
      <span className="truncate">{o.label}</span>
    </SelectItem>
  )
  const grouped = options.some((entry) => "options" in entry)
  return (
    <Select
      value={toRadix(value)}
      onValueChange={(v) => onValueChange((v === EMPTY ? "" : v) as T)}
      disabled={disabled}
      name={name}
    >
      <SelectTrigger
        id={id}
        size={size}
        className={cn(size === "default" && "w-full max-w-48", "min-w-0", className)}
        aria-label={ariaLabel ?? (typeof label === "string" ? label : undefined)}
        aria-describedby={ariaDescribedBy}
      >
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent className={contentClassName}>
        {grouped ? (
          options.map((entry, index) =>
            "options" in entry ? (
              <SelectGroup key={`group-${index}`}>
                <SelectLabel>{entry.label}</SelectLabel>
                {entry.options.map(item)}
              </SelectGroup>
            ) : (
              <SelectGroup key={`group-${index}`}>{item(entry)}</SelectGroup>
            )
          )
        ) : (
          <SelectGroup>
            {heading ? <SelectLabel>{heading}</SelectLabel> : null}
            {(options as SimpleSelectOption<T>[]).map(item)}
          </SelectGroup>
        )}
      </SelectContent>
    </Select>
  )
}

export {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectScrollDownButton,
  SelectScrollUpButton,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
  SimpleSelect,
}
