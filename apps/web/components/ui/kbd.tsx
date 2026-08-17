"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export function Kbd({
  className,
  ...rest
}: React.HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        "inline-flex h-5 min-w-5 items-center justify-center rounded border border-(--border) " +
          "bg-(--bg-base) px-1.5 font-mono text-[10px] text-(--fg-muted) shadow-(--shadow-sm)",
        className,
      )}
      {...rest}
    />
  );
}
