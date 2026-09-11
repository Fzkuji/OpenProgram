"use client";

/**
 * Custom @xyflow/react node shells for OpenProgram session projection.
 * Shapes: ROOT◇ user○ llm△ tool■ capsule◎ merge◉
 * HEAD = stroke emphasis on the same shape (no outer ring).
 * Coverage = white/bright fill via --text-bright.
 */

import { memo } from "react";
import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import type { ProjectionNode } from "@/lib/runtime-bridge/dag/xyflow";

export type DagRfNodeData = {
  proj: ProjectionNode;
};

export type DagRfNode = Node<DagRfNodeData, "dag">;

const SIZE = 22;

function Glyph({ proj }: { proj: ProjectionNode }) {
  const stroke = proj.isError
    ? "#e5534b"
    : (proj.isGhost || proj.status === "cancelled" || proj.status === "stopped")
      ? "var(--dag-ghost, #c9c7bf)"
      : proj.color;
  const fill = proj.inContext
    ? "var(--text-bright, #ffffff)"
    : "var(--bg-primary, #262624)";
  const sw = proj.isHead ? 2.6 : 2.0;
  const opacity = proj.aged ? 0.45 : (proj.onHead ? 1 : 0.55);
  const dash = proj.status === "running" ? "4 3" : undefined;
  const common = {
    fill,
    stroke,
    strokeWidth: sw,
    strokeDasharray: dash,
    opacity,
  } as const;

  const headClass = proj.isHead ? "dag-rf-head" : undefined;

  switch (proj.shape) {
    case "diamond":
      return (
        <rect
          className={headClass}
          x={5} y={5} width={12} height={12}
          transform="rotate(45 11 11)"
          rx={1}
          style={{ color: proj.color }}
          {...common}
        />
      );
    case "triangle":
      return (
        <polygon
          className={headClass}
          points="11,2.5 19.5,18.5 2.5,18.5"
          style={{ color: proj.color }}
          {...common}
        />
      );
    case "square":
      return (
        <rect
          className={headClass}
          x={4} y={4} width={14} height={14} rx={1.5}
          style={{ color: proj.color }}
          {...common}
        />
      );
    case "capsule":
      return (
        <g style={{ color: proj.color }}>
          <circle className={headClass} cx={11} cy={11} r={8} {...common} />
          <circle
            cx={11} cy={11} r={4.4}
            fill="none"
            stroke={stroke}
            strokeWidth={1.2}
            opacity={opacity}
          />
        </g>
      );
    case "merge_dot":
      return (
        <g style={{ color: proj.color }}>
          <circle className={headClass} cx={11} cy={11} r={8} {...common} />
          <circle
            cx={11} cy={11} r={3}
            fill="var(--bg-primary, #262624)"
            stroke={stroke}
            strokeWidth={1.4}
            opacity={opacity}
          />
        </g>
      );
    case "circle":
    default:
      return (
        <circle
          className={headClass}
          cx={11} cy={11} r={8}
          style={{ color: proj.color }}
          {...common}
        />
      );
  }
}

function DagNodeInner({ data }: NodeProps<DagRfNode>) {
  const proj = data.proj;
  const count = !proj.summaryOpen && proj.summaryCount
    ? proj.summaryCount
    : (!proj.threadOpen && proj.threadCount ? proj.threadCount : 0);

  return (
    <div
      className={
        "dag-rf-node history-node"
        + (proj.isHead ? " is-head" : "")
        + (proj.onHead ? "" : " off-head")
        + (proj.inContext ? "" : " out-of-context")
        + (proj.isSummary ? " is-summary" : "")
        + (proj.isGhost ? " is-ghost" : "")
        + (proj.status === "running" ? " is-running" : "")
      }
      data-msg-id={proj.id}
      data-summary={proj.summaryCount && !proj.isInert ? String(proj.summaryCount) : ""}
      data-summary-open={proj.summaryOpen ? "1" : "0"}
      data-thread={proj.threadCount ? String(proj.threadCount) : ""}
      data-thread-open={proj.threadOpen ? "1" : "0"}
      data-internal={proj.isInternal ? "1" : "0"}
      data-owner={proj.ownerId || ""}
      data-ghost={proj.isGhost ? "1" : "0"}
      title={proj.rangeChip || undefined}
      style={{ width: SIZE, height: SIZE, position: "relative" }}
    >
      {/* Invisible handles — edges need anchors; not connectable. */}
      <Handle type="target" position={Position.Top} className="dag-rf-handle" />
      <Handle type="source" position={Position.Bottom} className="dag-rf-handle" />
      <svg width={SIZE} height={SIZE} viewBox="0 0 22 22" aria-hidden="true">
        <Glyph proj={proj} />
      </svg>
      {count > 0 && (
        <span className="dag-rf-count" aria-hidden="true">{count}</span>
      )}
      {proj.isSummary && proj.rangeChip && proj.summaryOpen && (
        <span className="dag-rf-range-chip">{proj.rangeChip}</span>
      )}
      {proj.isError && (
        <span className="dag-rf-bang" aria-hidden="true">!</span>
      )}
      {proj.spilled && (
        <span className="dag-rf-spill" aria-hidden="true">▤</span>
      )}
    </div>
  );
}

export const DagNode = memo(DagNodeInner);

export const nodeTypes = {
  dag: DagNode,
};
