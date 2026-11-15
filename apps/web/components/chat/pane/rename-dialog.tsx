"use client";

/**
 * RenameDialog — one-field modal for renaming the current session from
 * the chat pane's `…` menu.
 *
 * The Recents row renames in place (an inline `<input>` swapped over the
 * row label, see `sidebar/sessions/conv-item.tsx`); that editor is bound to the row's
 * DOM and can't be driven from here, so the top-right menu gets the
 * modal form of the same action. Same WS verb either way.
 */

import { useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";

export function RenameDialog({
  initial,
  onSubmit,
  onCancel,
  id,
  preserveFocus = false,
}: {
  initial: string;
  onSubmit: (title: string) => void;
  onCancel: () => void;
  id?: string;
  preserveFocus?: boolean;
}) {
  const { t, text } = useTranslation();
  const [value, setValue] = useState(initial);

  function submit() {
    const clean = value.trim();
    if (!clean) {
      onCancel();
      return;
    }
    onSubmit(clean);
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onCancel();
      }}
    >
      <DialogContent {...(id ? { id } : {})}
        onCloseAutoFocus={preserveFocus ? (event) => event.preventDefault() : undefined}>
        <DialogHeader>
          <DialogTitle>{t("sidebar.rename")}</DialogTitle>
        </DialogHeader>
        <Input
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              submit();
            }
          }}
          aria-label={t("sidebar.rename")}
        />
        <DialogFooter>
          <Button variant="elevated" data-rename-action="cancel" onClick={onCancel}>
            {t("sidebar.cancel")}
          </Button>
          <Button onClick={submit} data-rename-action="save">
            {text("Save", "保存")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
