import { Check } from "lucide-react";

import { cn } from "@/lib/utils";
import { CHECK_SLOT } from "@/components/ui/menu/menu-styles";

/** The selected row's check in every menu: Nova's size-4 check, bold and
 *  in the accent, in the CHECK_SLOT column (CHECK_SLOT_PAD on other rows). */
export function MenuCheck({ className }: { className?: string }) {
  return <Check strokeWidth={2.5} className={cn("size-4", CHECK_SLOT, className)} aria-hidden="true" />;
}
