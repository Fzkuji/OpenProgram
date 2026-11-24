import type { ComponentProps } from "react";
import { LoaderCircle } from "lucide-react";

import { cn } from "@/lib/utils";

/** A turning circle. It sits next to text that already says what is running
 *  ("Checking the key…", a button's label), so on its own it is decorative and
 *  hidden from screen readers. A caller that shows it alone names it with
 *  `aria-label`, which makes it a status announcing that name. */
function Spinner({ className, ...props }: ComponentProps<"svg">) {
  const named = Boolean(props["aria-label"]);
  return (
    <LoaderCircle
      role={named ? "status" : undefined}
      aria-hidden={named ? undefined : true}
      className={cn("size-4 animate-spin", className)}
      {...props}
    />
  );
}

export { Spinner };
