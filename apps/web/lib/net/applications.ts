import { jsonFetch } from "./fetch-client";

export interface ApplicationDefinition {
  id: string;
  title: string;
  display_title?: string;
  /** A glyph name; components/applications/application-icon.tsx draws it. */
  icon?: string;
  /** One plain sentence about the application (application.json). */
  description?: string;
  digest: string;
  version: string;
  source: string;
  backend?: { kind: "python" };
  scope: "global" | "project";
  /** What the manifest asks for: storage.app, model.invoke, files.project.read/write. */
  capabilities?: string[];
  enabled: boolean;
  hidden?: boolean;
  /** Ships under openprogram/programs/applications/. */
  builtin?: boolean;
  ui: { root: string; entry: string };
}
/** A built-in application: its enabled setting, the tools it registered,
 *  and what it is called (title, one-sentence description, glyph). */
export interface BuiltinApplication {
  id: string;
  enabled: boolean;
  loaded: boolean;
  entries: string[];
  title?: string;
  description?: string;
  icon?: string;
}
export interface ApplicationInstance {
  ui_url: string;
  instance: { id: string; app_id: string; project_id: string };
  application: ApplicationDefinition;
  runs: ApplicationRun[];
}
export interface ApplicationRun { id: string; instance_id: string; status: string }
export const applicationRequest = <T>(url: string, method = "GET", body?: unknown) =>
  jsonFetch<T>(url, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
