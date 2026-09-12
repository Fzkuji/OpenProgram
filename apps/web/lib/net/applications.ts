import { jsonFetch } from "./fetch-client";

export interface ApplicationDefinition {
  id: string;
  title: string;
  display_title?: string;
  digest: string;
  version: string;
  source: string;
  backend?: { kind: "python" };
  scope: "global" | "project";
  enabled: boolean;
  hidden?: boolean;
  ui: { root: string; entry: string };
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
