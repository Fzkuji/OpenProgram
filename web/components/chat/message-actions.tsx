"use client";

import { useState } from "react";
import { Check, ChevronLeft, ChevronRight, Copy, Pencil, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { HoverTip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useTranslation } from "@/lib/i18n";

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
  const { text: tr } = useTranslation();
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
            aria-label={tr("Previous attempt", "上一个尝试")}
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
            aria-label={tr("Next attempt", "下一个尝试")}
          >
            <ChevronRight size={12} />
          </Button>
        </div>
      )}
      <HoverTip label={copied ? tr("Copied", "已复制") : tr("Copy", "复制")}>
        <Button variant="ghost" size="icon-sm" onClick={copy} aria-label={tr("Copy", "复制")}>
          {copied ? <Check size={12} /> : <Copy size={12} />}
        </Button>
      </HoverTip>
      {onEdit && (
        <HoverTip label={tr("Edit & resend", "编辑并重新发送")}>
          <Button variant="ghost" size="icon-sm" onClick={onEdit} aria-label={tr("Edit", "编辑")}>
            <Pencil size={12} />
          </Button>
        </HoverTip>
      )}
      {onRetry && (
        <HoverTip label={tr("Retry", "重试")}>
          <Button variant="ghost" size="icon-sm" onClick={onRetry} aria-label={tr("Retry", "重试")}>
            <RefreshCw size={12} />
          </Button>
        </HoverTip>
      )}
    </div>
  );
}
