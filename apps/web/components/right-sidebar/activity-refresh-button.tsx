"use client";

import { useEffect, useRef, useState } from "react";
import { Check, RefreshCw, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";

/** Manual feedback follows completed reads; background polling stays quiet. */
export function ActivityRefreshButton({ onRefresh, label, className }: {
  onRefresh: () => Promise<boolean>;
  label: string;
  className?: string;
}) {
  const { text } = useTranslation();
  const [status, setStatus] = useState<"idle" | "pending" | "success" | "error">("idle");
  const busy = useRef(false);
  const mounted = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; clearTimeout(timer.current); };
  }, []);
  const message = status === "pending" ? text("Refreshing…", "正在刷新…")
    : status === "success" ? text("Refreshed", "已刷新")
    : status === "error" ? text("Refresh failed. Try again.", "刷新失败，请重试。") : label;
  return <>
    <Button variant="ghost" size="icon" className={className} aria-label={message} title={message}
      disabled={status === "pending"} aria-busy={status === "pending"} onClick={async () => {
        if (busy.current) return;
        busy.current = true;
        clearTimeout(timer.current);
        setStatus("pending");
        let succeeded = false;
        try { succeeded = await onRefresh(); } catch { /* Existing notices retain the read error. */ }
        if (!mounted.current) return;
        busy.current = false;
        setStatus(succeeded ? "success" : "error");
        timer.current = setTimeout(() => setStatus("idle"), 1800);
      }}>
      {status === "success" ? <Check size={14} /> : status === "error" ? <TriangleAlert size={14} />
        : <RefreshCw size={14} className={status === "pending" ? "animate-spin motion-reduce:animate-none" : undefined} />}
    </Button>
    <span className="sr-only" role="status">{status === "idle" ? "" : message}</span>
  </>;
}
