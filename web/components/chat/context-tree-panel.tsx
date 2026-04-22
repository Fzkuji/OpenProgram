"use client";

import { useState } from "react";
import { ChevronRight, ChevronDown, Activity, X } from "lucide-react";
import type { TreeNode } from "@/lib/conv-store";
import { cn } from "@/lib/utils";

interface Props {
  tree: TreeNode | null;
  onClose: () => void;
}

export function ContextTreePanel({ tree, onClose }: Props) {
  const [selected, setSelected] = useState<TreeNode | null>(null);

  return (
    <aside
      className="flex h-screen w-[360px] shrink-0 flex-col border-l"
      style={{
        background: "var(--bg-secondary)",
        borderColor: "var(--border-color)",
      }}
    >
      <div
        className="flex h-12 items-center justify-between border-b px-4"
        style={{ borderColor: "var(--border-color)" }}
      >
        <div className="flex items-center gap-2">
          <Activity
            className="h-3.5 w-3.5"
            style={{ color: "var(--text-muted)" }}
          />
          <h3
            className="text-[13px] font-semibold"
            style={{ color: "var(--text-bright)" }}
          >
            Context Tree
          </h3>
        </div>
        <button onClick={onClose}>
          <X className="h-4 w-4" style={{ color: "var(--text-muted)" }} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {!tree ? (
          <div
            className="p-6 text-center text-[12px]"
            style={{ color: "var(--text-muted)" }}
          >
            No live execution. Run a function to see its Context tree here.
          </div>
        ) : (
          <div className="p-2">
            <TreeNodeView
              node={tree}
              depth={0}
              selected={selected}
              onSelect={setSelected}
            />
          </div>
        )}
      </div>

      {selected && (
        <div
          className="max-h-[40%] overflow-y-auto border-t p-4"
          style={{ borderColor: "var(--border-color)" }}
        >
          <NodeDetail node={selected} />
        </div>
      )}
    </aside>
  );
}

function TreeNodeView({
  node,
  depth,
  selected,
  onSelect,
}: {
  node: TreeNode;
  depth: number;
  selected: TreeNode | null;
  onSelect: (n: TreeNode) => void;
}) {
  const [open, setOpen] = useState(depth < 2);
  const hasChildren = !!node.children?.length;
  const label =
    (node.name as string) ||
    (node.type as string) ||
    (node.node_type as string) ||
    "node";
  const isSel = selected === node;

  const statusColor =
    node.status === "error"
      ? "var(--accent-red)"
      : node.status === "ok" || node.status === "done"
        ? "var(--accent-green)"
        : node._in_progress
          ? "var(--accent-blue)"
          : "var(--text-muted)";

  return (
    <div>
      <div
        onClick={() => onSelect(node)}
        className={cn(
          "flex cursor-pointer items-center gap-1 rounded py-1 pr-2 text-[12px]"
        )}
        style={{
          paddingLeft: depth * 12 + 4,
          background: isSel ? "var(--bg-tertiary)" : "transparent",
          color: isSel ? "var(--text-bright)" : "var(--text-primary)",
        }}
      >
        {hasChildren ? (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setOpen(!open);
            }}
            className="shrink-0"
          >
            {open ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
          </button>
        ) : (
          <span className="inline-block w-3" />
        )}
        <span
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ background: statusColor }}
        />
        <span className="truncate font-mono">{label}</span>
        {typeof node.elapsed_ms === "number" && (
          <span
            className="ml-auto shrink-0 text-[10px]"
            style={{ color: "var(--text-muted)" }}
          >
            {node.elapsed_ms > 1000
              ? (node.elapsed_ms / 1000).toFixed(1) + "s"
              : node.elapsed_ms + "ms"}
          </span>
        )}
      </div>
      {open && hasChildren && (
        <div>
          {node.children!.map((c, i) => (
            <TreeNodeView
              key={i}
              node={c}
              depth={depth + 1}
              selected={selected}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function NodeDetail({ node }: { node: TreeNode }) {
  const rawFields: [string, unknown][] = [
    ["name", node.name],
    ["type", node.type ?? node.node_type],
    ["status", node.status],
    ["elapsed", typeof node.elapsed_ms === "number" ? `${node.elapsed_ms}ms` : undefined],
  ];
  const fields = rawFields.filter(([, v]) => v !== undefined);

  return (
    <div className="space-y-3 text-[11px]">
      <div>
        {fields.map(([k, v]) => (
          <div key={k} className="flex gap-2">
            <span style={{ color: "var(--text-muted)" }}>{k}:</span>
            <span style={{ color: "var(--text-primary)" }}>{String(v)}</span>
          </div>
        ))}
      </div>
      {node.inputs && Object.keys(node.inputs).length > 0 && (
        <div>
          <div
            className="mb-1 text-[10px] font-semibold uppercase"
            style={{ color: "var(--text-muted)" }}
          >
            Inputs
          </div>
          <pre
            className="overflow-x-auto rounded p-2 font-mono text-[10px]"
            style={{
              background: "var(--bg-input)",
              color: "var(--text-primary)",
            }}
          >
            {JSON.stringify(node.inputs, null, 2)}
          </pre>
        </div>
      )}
      {node.outputs !== undefined && (
        <div>
          <div
            className="mb-1 text-[10px] font-semibold uppercase"
            style={{ color: "var(--text-muted)" }}
          >
            Output
          </div>
          <pre
            className="max-h-48 overflow-auto rounded p-2 font-mono text-[10px]"
            style={{
              background: "var(--bg-input)",
              color: "var(--text-primary)",
            }}
          >
            {typeof node.outputs === "string"
              ? node.outputs
              : JSON.stringify(node.outputs, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
