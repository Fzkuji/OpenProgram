"use client"

// The app's one confirmation dialog: Nova AlertDialog with a title, a
// description and Cancel / confirm buttons. Every "are you sure?" prompt
// renders through it so they share one look — the confirm button is the
// default Button, or the destructive one when the action deletes or
// lowers a safeguard. Callers mount it while the question is pending
// (open defaults to true) and get onCancel for Escape, a click outside
// the dialog and the Cancel button alike — never after onConfirm, although Radix also
// reports the confirm button's close through onOpenChange.

import * as React from "react"

import { useTranslation } from "@/lib/i18n"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"

export function ConfirmDialog({
  open = true,
  title,
  description,
  confirmLabel,
  cancelLabel,
  destructive = false,
  onConfirm,
  onCancel,
}: {
  open?: boolean
  title: React.ReactNode
  description?: React.ReactNode
  confirmLabel: React.ReactNode
  cancelLabel?: React.ReactNode
  destructive?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const { text } = useTranslation()
  const confirmed = React.useRef(false)
  React.useEffect(() => {
    if (open) confirmed.current = false
  }, [open])
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !confirmed.current) onCancel()
      }}
    >
      {/* Radix focuses Cancel on open, and the menu click that opened the
          dialog leaves the browser in keyboard focus mode, so Cancel wore
          its focus ring before anyone touched it. Focus the dialog itself;
          Tab still reaches both buttons and Escape still cancels. */}
      <AlertDialogContent
        onOverlayClick={onCancel}
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          ;(event.currentTarget as HTMLElement).focus()
        }}
      >
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          {description ? <AlertDialogDescription>{description}</AlertDialogDescription> : null}
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{cancelLabel ?? text("Cancel", "取消")}</AlertDialogCancel>
          <AlertDialogAction
            variant={destructive ? "destructive" : "default"}
            onClick={() => {
              confirmed.current = true
              onConfirm()
            }}
          >
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

// --- confirmAction(): the promise form, for code that used to call the
// browser's window.confirm. One <ConfirmDialogHost/> (app/providers.tsx)
// renders the pending question; each call resolves true on confirm and
// false on cancel. A new question while one is open cancels the old one.

export type ConfirmRequest = {
  title: string
  description?: string
  confirmLabel: string
  cancelLabel?: string
  destructive?: boolean
}

type Pending = ConfirmRequest & { id: number; resolve: (ok: boolean) => void }

let nextId = 0
let pending: Pending | null = null
const listeners = new Set<(p: Pending | null) => void>()

function publish(next: Pending | null) {
  pending = next
  for (const listener of listeners) listener(next)
}

export function confirmAction(request: ConfirmRequest): Promise<boolean> {
  return new Promise((resolve) => {
    pending?.resolve(false)
    publish({ ...request, id: ++nextId, resolve })
  })
}

export function ConfirmDialogHost() {
  const [current, setCurrent] = React.useState<Pending | null>(pending)
  React.useEffect(() => {
    listeners.add(setCurrent)
    return () => { listeners.delete(setCurrent) }
  }, [])
  if (!current) return null
  const settle = (ok: boolean) => {
    if (pending === current) publish(null)
    current.resolve(ok)
  }
  return (
    <ConfirmDialog
      key={current.id}
      title={current.title}
      description={current.description}
      confirmLabel={current.confirmLabel}
      cancelLabel={current.cancelLabel}
      destructive={current.destructive}
      onConfirm={() => settle(true)}
      onCancel={() => settle(false)}
    />
  )
}
