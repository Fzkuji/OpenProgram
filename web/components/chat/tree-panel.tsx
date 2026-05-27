"use client";

import { useState } from "react";
import { ChevronRight, X } from "lucide-react";
import type { ContextTreeNode } from "@/lib/types";
import { cn, formatDuration } from "@/lib/utils";
import { Button } from "@/components/ui/button";

interface Props {
  tree: ContextTreeNode | null;
  onClose: () => void;
}

/** Right-rail panel that visualizes the live execution tree. */
export function TreePanel({ tree, onClose }: Props) {
  return (
    <aside className="flex h-full w-80 shrink-0 flex-col border-l border-(--border) bg-(--bg-elevated)">
      <div className="flex h-12 items-center justify-between border-b border-(--border) px-3">
        <span className="text-[13px] font-medium text-(--fg)">Execution tree</span>
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close">
          <X size={14} />
        </Button>
      </div>
      <div className="scroll-y min-h-0 flex-1 p-2">
        {!tree ? (
          <p className="px-2 py-3 text-xs text-(--fg-subtle)">
            No execution yet. Start a function or chat with tools enabled.
          </p>
        ) : (
          <TreeRow node={tree} depth={0} />
        )}
      </div>
    </aside>
  );
}

function TreeRow({ node, depth }: { node: ContextTreeNode; depth: number }) {
  const [open, setOpen] = useState(depth < 2);
  const [showDetails, setShowDetails] = useState(false);
  const children = Array.isArray(node.children) ? node.children : [];
  const status = (node.status as string | undefined) ?? "";
  const running = node._in_progress || status === "running";
  const failed = status === "error" || status === "failed";
  const name =
    (node.name as string) ||
    (node.node_type as string) ||
    (node.type as string) ||
    "node";

  return (
    <div>
      <div
        className="flex items-center gap-1.5 rounded-md py-0.5 pl-1.5 pr-2 text-[12px] hover:bg-(--bg-hover)"
        style={{ paddingLeft: 6 + depth * 12 }}
      >
        <button
          onClick={() => setOpen((o) => !o)}
          className={cn(
            "flex h-4 w-4 shrink-0 items-center justify-center rounded text-(--fg-subtle)",
            children.length === 0 && "invisible",
          )}
          aria-label={open ? "Collapse" : "Expand"}
        >
          <ChevronRight
            size={11}
            className={cn("transition-transform", open && "rotate-90")}
          />
        </button>
        <StatusDot running={!!running} failed={failed} />
        <button
          onClick={() => setShowDetails((s) => !s)}
          className="flex flex-1 items-center gap-2 truncate text-left"
        >
          <span className="truncate font-mono text-(--fg)">{name}</span>
          {typeof node.elapsed_ms === "number" && (
            <span className="ml-auto shrink-0 text-(--fg-subtle)">
              {formatDuration(node.elapsed_ms)}
            </span>
          )}
        </button>
      </div>
      {showDetails && (node.inputs || node.outputs !== undefined) && (
        <div
          className="ml-6 mt-1 mb-1.5 rounded-md border border-(--border) bg-(--bg-base) px-2 py-1.5 text-[11px]"
          style={{ marginLeft: 22 + depth * 12 }}
        >
          {node.inputs && <Detail label="inputs" value={node.inputs} />}
          {node.outputs !== undefined && <Detail label="outputs" value={node.outputs} />}
        </div>
      )}
      {open && children.length > 0 && (
        <div>
          {children.map((c, i) => (
            <TreeRow key={(c.id as string) || `${depth}-${i}`} node={c} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: unknown }) {
  let formatted: string;
  try {
    formatted = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  } catch {
    formatted = String(value);
  }
  return (
    <div className="mt-1 first:mt-0">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-(--fg-subtle)">
        {label}
      </div>
      <pre className="mt-0.5 whitespace-pre-wrap break-words font-mono text-[11px] text-(--fg-muted)">
        {formatted}
      </pre>
    </div>
  );
}

function StatusDot({ running, failed }: { running: boolean; failed: boolean }) {
  return (
    <span
      className={cn(
        "inline-block h-1.5 w-1.5 shrink-0 rounded-full",
        running && "animate-pulse",
      )}
      style={{
        background: failed
          ? "var(--danger)"
          : running
            ? "var(--accent)"
            : "var(--success)",
      }}
    />
  );
}
