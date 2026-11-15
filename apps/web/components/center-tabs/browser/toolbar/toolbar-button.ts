import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import styles from "../browser.module.css";

/** The browser toolbar's icon button: the official shadcn ghost icon Button
 *  (components/ui/button.tsx, `icon-sm` so its svg rule gives the toolbar's
 *  14px icons), held at the toolbar's own 26px by `.webToolbarBtn`, which
 *  carries geometry only. Pass further module classes (`webToolbarMedium`,
 *  `webToolbarForward`, `webStopLoading`, …) as arguments; falsy values are
 *  dropped. The floating web-tab preview header uses the same variant at
 *  `icon-xs` (web-tab-pip.tsx). */
export function webToolbarButton(...extra: Array<string | false | null | undefined>): string {
  return cn(buttonVariants({ variant: "ghost", size: "icon-sm" }), styles.webToolbarBtn, ...extra);
}
