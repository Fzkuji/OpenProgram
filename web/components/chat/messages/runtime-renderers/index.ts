/**
 * Per-function RuntimeBlock return-renderer registry.
 *
 * The legacy RuntimeBlock body ran a single `distillReturn(rawOutput)`
 * + markdown render for every agentic function. That one-size-fits-all
 * formatter throws away structure (sources, file lists, step counts,
 * success flags). This registry lets each function ship its own React
 * component that knows the shape of its return JSON.
 *
 * Add a new renderer:
 *   1. Drop a `<fn>.tsx` next to this file exporting a component
 *      typed as RuntimeRenderer (and optionally a RuntimePreview for
 *      the header "-> ..." line).
 *   2. Register it in `RENDERERS` / `PREVIEWS` below.
 *
 * Unknown functions fall back to DefaultRenderer (the original
 * distillReturn behaviour) — fn-form runs are unaffected.
 *
 * TODO: research_agent ({report, sources, citations}) and wiki_agent
 * ({operation, files_changed, links_added}) want bespoke renderers
 * once their actual return shapes settle.
 */
import { DefaultRenderer } from "./default";
import { GuiAgentRenderer, guiAgentPreview } from "./gui-agent";
import type { RuntimePreview, RuntimeRenderer } from "./types";

const RENDERERS: Record<string, RuntimeRenderer> = {
  gui_agent: GuiAgentRenderer,
};

const PREVIEWS: Record<string, RuntimePreview> = {
  gui_agent: guiAgentPreview,
};

export function pickRenderer(fnName: string): RuntimeRenderer {
  return RENDERERS[fnName] ?? DefaultRenderer;
}

export function pickPreview(fnName: string): RuntimePreview | null {
  return PREVIEWS[fnName] ?? null;
}

export { DefaultRenderer, distillReturn } from "./default";
export type { RuntimeRenderer, RuntimeRendererProps, RuntimePreview } from "./types";
