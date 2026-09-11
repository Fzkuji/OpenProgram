/**
 * Phase 1→2 product spike: center DAG uses @xyflow/react.
 * Flip to false to restore the imperative SVG emitter.
 */
export const USE_XYFLOW_CANVAS = true;

export function isXyflowCanvas(): boolean {
  return USE_XYFLOW_CANVAS;
}
