/**
 * Shared types for per-function RuntimeBlock return-renderers.
 *
 * Each renderer receives the raw stringified return value (what the
 * agentic function actually emitted into `msg.content`) together with
 * the Execution DAG root (so the renderer can read structured params
 * or sub-results when the raw output is too lossy).
 */
import type { ComponentType } from "react";

export interface RuntimeRendererProps {
  rawOutput: string;
  contextTree: unknown;
  fnName: string;
}

export type RuntimeRenderer = ComponentType<RuntimeRendererProps>;

/**
 * The header preview line ("-> ..."). Some renderers want to override
 * this independent of the body — e.g. gui_agent prefers
 * "success · 12 steps · 4.3s" over the long summary text. Returning
 * `null` falls back to the default one-line distillation.
 */
export type RuntimePreview = (props: RuntimeRendererProps) => string | null;
