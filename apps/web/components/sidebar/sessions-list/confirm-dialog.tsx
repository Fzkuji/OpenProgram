"use client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";

/** Modal confirm — shadcn <Dialog>. */
export function ConfirmDialog({
  title,
  message,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();

  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onCancel();
      }}
    >
      <DialogContent
        className="max-w-[400px] border-0"
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{message}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="secondary"
            onClick={onCancel}
            className="rounded-full bg-[var(--bg-selected)] text-[var(--text-bright)] transition-[filter] hover:bg-[var(--bg-selected)] hover:brightness-125"
          >
            {t("sidebar.cancel")}
          </Button>
          <Button
            variant="destructive"
            onClick={onConfirm}
            className="rounded-full hover:bg-[#c9413a]"
          >
            {t("sidebar.delete")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

