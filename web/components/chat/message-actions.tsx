"use client";

import { useState } from "react";
import { Check, ChevronLeft, ChevronRight, Copy, Pencil, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { HoverTip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

interface SiblingNav {
  index: number;
  total: number;
  onPrev?: () => void;
  onNext?: () => void;
}

interface Props {
  text: string;
  onEdit?: () => void;
  onRetry?: () => void;
  sibling?: SiblingNav;
  className?: string;
}

export function MessageActions({ text, onEdit, onRetry, sibling, className }: Props) {
  const [copied, setCopied] = useState(false);

  function copy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    });
  }

  return (
    <div className={cn("flex items-center gap-0.5", className)}>
      {sibling && sibling.total > 1 && (
        <div className="mr-1 flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={sibling.onPrev}
            disabled={!sibling.onPrev}
            aria-label="Previous attempt"
          >
            <ChevronLeft size={12} />
          </Button>
          <span className="font-mono text-[11px] text-(--fg-subtle)">
            {sibling.index + 1}/{sibling.total}
          </span>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={sibling.onNext}
            disabled={!sibling.onNext}
            aria-label="Next attempt"
          >
            <ChevronRight size={12} />
          </Button>
        </div>
      )}
      <HoverTip label={copied ? "Copied" : "Copy"}>
        <Button variant="ghost" size="icon-sm" onClick={copy} aria-label="Copy">
          {copied ? <Check size={12} /> : <Copy size={12} />}
        </Button>
      </HoverTip>
      {onEdit && (
        <HoverTip label="Edit & resend">
          <Button variant="ghost" size="icon-sm" onClick={onEdit} aria-label="Edit">
            <Pencil size={12} />
          </Button>
        </HoverTip>
      )}
      {onRetry && (
        <HoverTip label="Retry">
          <Button variant="ghost" size="icon-sm" onClick={onRetry} aria-label="Retry">
            <RefreshCw size={12} />
          </Button>
        </HoverTip>
      )}
    </div>
  );
}
