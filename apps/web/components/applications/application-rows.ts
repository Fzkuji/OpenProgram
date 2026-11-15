/**
 * The rows of the Applications page: the built-in applications and the
 * installed ones in one list, each with the one title, description and
 * icon every surface shows for it.
 */

import type { ApplicationDefinition, BuiltinApplication } from "@/lib/net/applications";
import type { BuiltinPage } from "@/lib/tabs/center-tab-ids";

export type ApplicationFilter = "all" | "builtin" | "installed";

export interface ApplicationRow {
  key: string;
  /** The id the application API takes. */
  id: string;
  title: string;
  description: string;
  icon?: string;
  version: string;
  scope: "global" | "project";
  builtin: boolean;
  enabled: boolean;
  source: string;
  /** Its window, when it has one. */
  definition?: ApplicationDefinition;
  /** A built-in application that opens as one of the App's own tabs. */
  builtinTab?: BuiltinPage;
}

/** Built-in applications that are one of the App's own tabs. */
export const BUILTIN_TABS: Partial<Record<string, BuiltinPage>> = {
  browser: "browser", office_editor: "office", workflow_recording: "recording", meeting: "meeting",
};

function words(id: string): string {
  const spaced = id.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function applicationRows(listed: ApplicationDefinition[], builtin: BuiltinApplication[]): ApplicationRow[] {
  const rows: ApplicationRow[] = [];
  for (const application of builtin) {
    const definition = listed.find(item => item.builtin && item.id === application.id);
    rows.push({
      key: `builtin:${application.id}`,
      id: application.id,
      title: definition?.display_title || definition?.title || application.title || words(application.id),
      description: definition?.description || application.description || "",
      icon: definition?.icon || application.icon,
      version: "",
      scope: definition?.scope ?? "global",
      builtin: true,
      enabled: application.enabled,
      source: "",
      definition,
      builtinTab: definition ? undefined : BUILTIN_TABS[application.id],
    });
  }
  for (const application of listed) {
    if (application.builtin) continue;
    rows.push({
      key: `app:${application.id}`,
      id: application.id,
      title: application.display_title || application.title,
      description: application.description || "",
      icon: application.icon,
      version: application.version,
      scope: application.scope,
      builtin: false,
      enabled: application.enabled,
      source: application.source,
      definition: application,
    });
  }
  return rows.sort((left, right) => left.title.localeCompare(right.title, undefined, { sensitivity: "base" }));
}
