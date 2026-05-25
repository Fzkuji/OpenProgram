/**
 * Shared types for the web-search provider settings pane.
 */

export interface SearchProvider {
  id: string;
  name: string;
  description: string;
  /**
   * Catalog metadata sourced from
   * ``openprogram.tools.web_search.catalog``. Optional on the wire
   * because older builds of the API endpoint didn't return these
   * fields — UI degrades gracefully (hides Setup block) when absent.
   */
  tier?: string;
  signup_url?: string | null;
  docs_url?: string | null;
  setup_steps?: string[];
  priority: number;
  env_var: string | null;
  configured: boolean;
  available: boolean;
  is_default: boolean;
}
