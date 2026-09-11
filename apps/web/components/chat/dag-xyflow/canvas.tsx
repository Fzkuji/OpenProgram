"use client";

/**
 * Controlled @xyflow/react canvas for the session DAG perspective.
 * Projection comes from the existing layout pipeline; intents go through
 * ContextGraphPort (xyflow/port.ts) → checkout / fold / detail.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
} from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  MiniMap,
  useReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeMouseHandler,
  SelectionMode,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import {
  getProjection,
  subscribeProjection,
  registerViewportApi,
  writeZoomReadout,
  handleNodeClick,
  handleNodeDblClick,
  handleNodeContextMenu,
  type ProjectionNode,
  type CanvasProjection,
} from "@/lib/runtime-bridge/dag/xyflow";
import { renderHistoryGraph } from "@/lib/runtime-bridge/dag";
import { _lastGraph, _lastHeadId } from "@/lib/runtime-bridge/dag/store/globals";
import { nodeTypes, type DagRfNodeData } from "./nodes";

const NODE_HALF = 11;

function toFlow(proj: CanvasProjection): {
  nodes: Node<DagRfNodeData>[];
  edges: Edge[];
} {
  const nodes: Node<DagRfNodeData>[] = proj.nodes.map((n) => ({
    id: n.id,
    type: "dag",
    position: { x: n.x - NODE_HALF, y: n.y - NODE_HALF },
    data: { proj: n },
    draggable: false,
    connectable: false,
    selectable: true,
  }));
  const edges: Edge[] = proj.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    type: e.kind === "fork" ? "smoothstep" : "default",
    animated: e.kind === "attach",
    style: {
      stroke: e.color,
      strokeWidth: e.kind === "thread" ? 1 : 1.6,
      strokeDasharray: e.dashed
        ? (e.kind === "thread" ? "2 3" : "4 3")
        : undefined,
      opacity: e.kind === "thread" ? 0.55 : 0.9,
    },
    selectable: false,
    focusable: false,
  }));
  return { nodes, edges };
}

function useProjection(): CanvasProjection {
  return useSyncExternalStore(
    subscribeProjection,
    getProjection,
    getProjection,
  );
}

function ViewportBridge({ sessionId }: { sessionId: string | null }) {
  const rf = useReactFlow();
  const lastSession = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    registerViewportApi({
      fitView: (opts) => { void rf.fitView(opts); },
      zoomIn: (opts) => { void rf.zoomIn(opts); },
      zoomOut: (opts) => { void rf.zoomOut(opts); },
      setZoom: (zoom) => {
        const vp = rf.getViewport();
        void rf.setViewport({ ...vp, zoom }, { duration: 160 });
      },
      getZoom: () => rf.getZoom(),
    });
    writeZoomReadout(rf.getZoom());
    return () => registerViewportApi(null);
  }, [rf]);

  useEffect(() => {
    if (sessionId !== lastSession.current) {
      lastSession.current = sessionId;
      requestAnimationFrame(() => {
        void rf.fitView({ padding: 0.18, duration: 0 });
        writeZoomReadout(rf.getZoom());
      });
    }
  }, [sessionId, rf]);

  return null;
}

function DagXyflowInner() {
  const proj = useProjection();
  const { nodes, edges } = useMemo(
    () => toFlow(proj),
    [proj],
  );

  const rerender = useCallback(() => {
    if (_lastGraph) renderHistoryGraph(_lastGraph, _lastHeadId);
  }, []);

  const onNodeClick = useCallback<NodeMouseHandler>((_evt, node) => {
    const p = (node.data as DagRfNodeData).proj as ProjectionNode;
    handleNodeClick(p, rerender);
  }, [rerender]);

  const onNodeDoubleClick = useCallback<NodeMouseHandler>((_evt, node) => {
    const p = (node.data as DagRfNodeData).proj as ProjectionNode;
    handleNodeDblClick(p, rerender);
  }, [rerender]);

  const onNodeContextMenu = useCallback<NodeMouseHandler>((evt, node) => {
    evt.preventDefault();
    const data = node.data as DagRfNodeData;
    handleNodeContextMenu(data.proj, evt.currentTarget as Element);
  }, []);

  if (proj.skeleton) {
    return (
      <div className="history-skeleton">
        {[70, 52, 61].map((w) => (
          <div
            key={w}
            className="history-skeleton-bar"
            style={{ width: `${w}%` }}
          />
        ))}
      </div>
    );
  }

  if (proj.empty) {
    return <div className="history-empty">No messages yet.</div>;
  }

  return (
    <div className="dag-xyflow-root">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        selectionOnDrag
        selectionMode={SelectionMode.Partial}
        panOnDrag={[1, 2]}
        panOnScroll
        zoomOnScroll={false}
        zoomOnPinch
        zoomOnDoubleClick={false}
        minZoom={0.25}
        maxZoom={3}
        proOptions={{ hideAttribution: true }}
        onNodeClick={onNodeClick}
        onNodeDoubleClick={onNodeDoubleClick}
        onNodeContextMenu={onNodeContextMenu}
        onMove={(_, vp) => writeZoomReadout(vp.zoom)}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        colorMode="dark"
      >
        <Background
          id="dag-lattice"
          variant={BackgroundVariant.Dots}
          gap={32}
          size={1.4}
          color="var(--border-light, rgba(255,255,255,0.10))"
        />
        <MiniMap
          className="dag-rf-minimap"
          pannable
          zoomable
          maskColor="rgba(0,0,0,0.45)"
          nodeColor={(n) =>
            (n.data as DagRfNodeData)?.proj?.color || "#666"
          }
        />
        <ViewportBridge sessionId={proj.sessionId} />
      </ReactFlow>
    </div>
  );
}

export function DagXyflowCanvas() {
  return (
    <ReactFlowProvider>
      <DagXyflowInner />
    </ReactFlowProvider>
  );
}
