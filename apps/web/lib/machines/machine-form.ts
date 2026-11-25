/**
 * Machines as the worker sends them, and the Settings form built from them.
 *
 * A machine has one connection: how it is reached. Its kind is the
 * connection method (SSH, Docker, an OSWorld server, another computer's
 * OpenProgram…), and the worker sends every method it knows with the JSON
 * Schema of its settings. The form is rendered from that schema alone: this
 * module names no method, so a method the worker adds later needs no change
 * here. What a feature can do there is the worker's to say: a machine
 * commands run on carries `shell`.
 *
 * Pure functions with no imports, so tests load it straight from source.
 */

/** This computer: built in, never stored, never removed or switched off. */
export const LOCAL_ID = "local";

/** What a stored secret setting reads as on the wire; the value never leaves the worker. */
export const SECRET_MARKER = { secret: true } as const;

/** Keys the worker adds to a connection for display; the rest are its settings. */
const DISPLAY_KEYS = new Set(["kind", "label", "label_zh", "where", "known"]);

export interface MachineConnection {
  kind: string;
  /** The method's label in English, or the stored method name when this installation does not know it. */
  label: string;
  label_zh?: string;
  /** What this connection reaches, in no language: "me@devbox", an image, an address; "" for this computer. */
  where: string;
  /** False when this installation has no such method: shown as stored, never edited. */
  known: boolean;
  [setting: string]: unknown;
}

/** How a machine answers now: this computer is always connected, a switched-off one disconnected. */
export type MachineState = "connected" | "connecting" | "disconnected" | "error";

export interface MachineStatus {
  state: MachineState;
  message?: string;
}

/** What the worker's shell feature says of a machine commands run on. */
export interface MachineShell {
  /** It keeps its own files (SSH): a project folder there is chosen on it. */
  remote_files: boolean;
  /** "conversation" when each conversation gets its own instance there (Docker). */
  instance_scope: string;
}

export interface MachineInfo {
  id: string;
  name: string;
  enabled: boolean;
  discovered?: boolean;
  /** Its connection state, checked by the worker behind each listing. */
  status?: MachineStatus;
  /** How it is reached. */
  connection?: MachineConnection;
  /** Present when commands and files run on it. */
  shell?: MachineShell;
}

/** A schema's title and description in Chinese, next to the English ones. */
export interface ChineseText {
  title?: string;
  description?: string;
}

/** The locale the page shows: Chinese text is used when it is "zh" and the method carries it. */
export type Locale = string;

export interface SettingSchema {
  type?: string | string[];
  title?: string;
  description?: string;
  "x-zh"?: ChineseText;
  examples?: unknown[];
  default?: unknown;
  pattern?: string;
  enum?: unknown[];
  secret?: boolean;
}

export interface KindSchema {
  type?: string;
  /** What the method needs or does, shown as the hint of its picker. */
  description?: string;
  "x-zh"?: ChineseText;
  properties?: Record<string, SettingSchema>;
  required?: string[];
}

/** One connection method. */
export interface KindInfo {
  name: string;
  label: string;
  label_zh?: string;
  settings: KindSchema;
  /** Only this computer is reached this way. */
  local_only: boolean;
  /** Added by pairing, never in the form (another computer's OpenProgram). */
  paired?: boolean;
}

export interface MachinesSnapshot {
  items: MachineInfo[];
  kinds: KindInfo[];
}

export function isSecretMarker(value: unknown): boolean {
  return !!value && typeof value === "object" && (value as { secret?: unknown }).secret === true;
}

/** A conversation can run on it: switched on, and commands run on it. */
export function usableAsEnvironment(machine: MachineInfo | null | undefined): boolean {
  return !!machine && machine.enabled !== false && !!machine.shell;
}

/** The machines the composer offers as a conversation's environment, in list order. */
export function environmentMachines(items: MachineInfo[]): MachineInfo[] {
  return items.filter(usableAsEnvironment);
}

/** Why no project can be on a machine, or "" when one can. */
export function notAnEnvironment(machine: MachineInfo): "off" | "no_shell" | "unknown_shell" | "" {
  if (machine.enabled === false) return "off";
  if (machine.connection?.known === false) return "unknown_shell";
  if (!machine.shell) return "no_shell";
  return "";
}

/** Another computer's OpenProgram: its conversations are listed here and run there. */
export function isRemoteOpenProgram(machine: MachineInfo | null | undefined): boolean {
  return machine?.connection?.kind === "openprogram";
}

/** True when its connection names a method this installation does not have. */
export function hasUnknownConnection(machine: MachineInfo): boolean {
  return machine.connection?.known === false;
}

export function findKind(kinds: KindInfo[], name: string): KindInfo | null {
  return kinds.find((kind) => kind.name === name) ?? null;
}

function chinese(locale: Locale | undefined): boolean {
  return locale === "zh";
}

/** A method's label in the page's language, English where the method carries no Chinese. */
export function kindLabel(kind: KindInfo, locale?: Locale): string {
  return (chinese(locale) && kind.label_zh) || kind.label || kind.name;
}

/** What a method needs or does, in the page's language: the hint of its picker. */
export function kindHint(kind: KindInfo | null, locale?: Locale): string {
  if (!kind) return "";
  return (chinese(locale) && kind.settings?.["x-zh"]?.description) || kind.settings?.description || "";
}

/** A connection in one line, "<label> · <where>": the method's label in the page's language, then what it reaches. */
export function connectionSummary(connection: MachineConnection, kinds: KindInfo[] = [], locale?: Locale): string {
  const kind = findKind(kinds, connection.kind);
  const label = (kind && chinese(locale) && kind.label_zh) || connection.label || connection.kind;
  const where = connection.where || "";
  return where ? `${label} · ${where}` : label;
}

/** The methods the form offers: not this computer's own, not one added by pairing. */
export function kindOptions(kinds: KindInfo[]): KindInfo[] {
  return kinds.filter((kind) => !kind.local_only && !kind.paired);
}

export type FieldType = "text" | "number" | "integer" | "boolean" | "choice";

export interface SettingField {
  key: string;
  label: string;
  hint: string;
  placeholder: string;
  required: boolean;
  pattern: string;
  secret: boolean;
  type: FieldType;
  choices: string[];
  default: string | boolean | undefined;
}

function schemaType(spec: SettingSchema): string {
  const type = Array.isArray(spec.type) ? spec.type.find((item) => item !== "null") : spec.type;
  return type || "string";
}

/** One form field per property of the method's settings schema, in the schema's order; its title and
 *  description in Chinese when the page is and the schema carries them. */
export function settingFields(kind: KindInfo | null, locale?: Locale): SettingField[] {
  if (!kind) return [];
  const properties = kind.settings?.properties ?? {};
  const required = new Set(kind.settings?.required ?? []);
  return Object.entries(properties).map(([key, spec]) => {
    const type = schemaType(spec);
    const choices = Array.isArray(spec.enum) ? spec.enum.map(String) : [];
    const fieldType: FieldType = choices.length
      ? "choice"
      : type === "boolean" ? "boolean"
        : type === "integer" ? "integer"
          : type === "number" ? "number" : "text";
    const example = Array.isArray(spec.examples) && spec.examples.length ? String(spec.examples[0]) : "";
    const fallback = spec.default;
    const zh = chinese(locale) ? spec["x-zh"] : undefined;
    return {
      key,
      label: zh?.title || spec.title || key,
      hint: zh?.description || spec.description || "",
      placeholder: example,
      required: required.has(key),
      pattern: typeof spec.pattern === "string" ? spec.pattern : "",
      secret: spec.secret === true,
      type: fieldType,
      choices,
      default: fallback === undefined || fallback === null
        ? undefined
        : typeof fallback === "boolean" ? fallback : String(fallback),
    };
  });
}

export interface ConnectionDraft {
  /** "" until a method is picked. */
  kind: string;
  values: Record<string, string | boolean>;
  /** Secret settings the worker holds: left blank, they are sent back as the marker. */
  held: string[];
}

export interface MachineDraft {
  id: string;
  name: string;
  /** Editing a machine that exists: its id is fixed. */
  editing: boolean;
  /** As the machine was when Edit was pressed. The switch on the row changes it, the form never does:
   *  Save sends the machine's state at that moment, see `withEnabled`. */
  enabled: boolean;
  connection: ConnectionDraft;
}

const NO_CONNECTION: ConnectionDraft = { kind: "", values: {}, held: [] };

export function emptyDraft(): MachineDraft {
  return { id: "", name: "", editing: false, enabled: true, connection: NO_CONNECTION };
}

/** A connection draft for a freshly picked method: every field at its default. */
export function connectionDraft(kinds: KindInfo[], kindName: string): ConnectionDraft {
  if (!kindName) return NO_CONNECTION;
  const values: Record<string, string | boolean> = {};
  for (const field of settingFields(findKind(kinds, kindName))) {
    if (field.default !== undefined) values[field.key] = field.default;
    else if (field.type === "boolean") values[field.key] = false;
  }
  return { kind: kindName, values, held: [] };
}

/** The form for a machine that exists, filled with its stored settings. */
export function draftFromMachine(machine: MachineInfo): MachineDraft {
  const draft: MachineDraft = {
    ...emptyDraft(),
    id: machine.id,
    name: machine.name,
    editing: true,
    enabled: machine.enabled !== false,
  };
  const connection = machine.connection;
  if (!connection) return draft;
  const values: Record<string, string | boolean> = {};
  const held: string[] = [];
  for (const [key, value] of Object.entries(connection)) {
    if (DISPLAY_KEYS.has(key) || value === null || value === undefined) continue;
    if (isSecretMarker(value)) held.push(key);
    else values[key] = typeof value === "boolean" ? value : String(value);
  }
  return { ...draft, connection: { kind: connection.kind, values, held } };
}

export type DraftProblem =
  | { field: "id"; problem: "required" | "taken" }
  | { field: "connection"; problem: "none" }
  | { field: string; problem: "required" | "pattern" | "number" | "integer"; label: string };

function blank(value: string | boolean | undefined): boolean {
  return value === undefined || (typeof value === "string" && value.trim() === "");
}

/** What keeps the form from being sent; the worker checks the same schema again. */
export function draftProblems(draft: MachineDraft, kinds: KindInfo[], takenIds: string[], locale?: Locale): DraftProblem[] {
  const problems: DraftProblem[] = [];
  if (!draft.editing) {
    // Without an id the worker makes one from the name, so one of the two is enough.
    const id = draft.id.trim().toLowerCase();
    if (!id && !draft.name.trim()) problems.push({ field: "id", problem: "required" });
    else if (id && takenIds.includes(id)) problems.push({ field: "id", problem: "taken" });
  }
  const connection = draft.connection;
  if (!connection.kind) {
    problems.push({ field: "connection", problem: "none" });
    return problems;
  }
  for (const field of settingFields(findKind(kinds, connection.kind), locale)) {
    const value = connection.values[field.key];
    const path = `connection.${field.key}`;
    if (blank(value)) {
      if (field.required && !(field.secret && connection.held.includes(field.key))) {
        problems.push({ field: path, problem: "required", label: field.label });
      }
      continue;
    }
    if (typeof value !== "string") continue;
    const text = value.trim();
    if (field.type === "integer" && !/^-?\d+$/.test(text)) {
      problems.push({ field: path, problem: "integer", label: field.label });
    } else if (field.type === "number" && !Number.isFinite(Number(text))) {
      problems.push({ field: path, problem: "number", label: field.label });
    } else if (field.pattern && !patternMatches(field.pattern, text)) {
      problems.push({ field: path, problem: "pattern", label: field.label });
    }
  }
  return problems;
}

/** JSON Schema's pattern: an ECMA-262 regular expression, matched anywhere in the value. */
function patternMatches(pattern: string, value: string): boolean {
  try {
    return new RegExp(pattern, "u").test(value);
  } catch {
    return true;
  }
}

/** The connection as the wire takes it: {kind, ...settings}, secrets kept as the marker when untouched. */
function connectionPayload(kinds: KindInfo[], draft: ConnectionDraft): Record<string, unknown> {
  const payload: Record<string, unknown> = { kind: draft.kind };
  for (const field of settingFields(findKind(kinds, draft.kind))) {
    const value = draft.values[field.key];
    if (blank(value)) {
      if (field.secret && draft.held.includes(field.key)) payload[field.key] = { ...SECRET_MARKER };
      continue;
    }
    if (typeof value === "boolean") payload[field.key] = value;
    else if (field.type === "integer" || field.type === "number") payload[field.key] = Number(value.trim());
    else payload[field.key] = field.secret ? value : value.trim();
  }
  return payload;
}

/** The draft with the machine's switch as it is now: the row's switch may have changed it since Edit. */
export function withEnabled(draft: MachineDraft, items: MachineInfo[]): MachineDraft {
  if (!draft.editing) return draft;
  const current = items.find((machine) => machine.id === draft.id);
  return current ? { ...draft, enabled: current.enabled !== false } : draft;
}

/** The `machine` of a `save_machine` request; a new machine without an id gets one the worker makes from its name. */
export function draftPayload(draft: MachineDraft, kinds: KindInfo[]): Record<string, unknown> {
  const payload: Record<string, unknown> = { name: draft.name.trim() };
  const id = draft.id.trim().toLowerCase();
  if (id) payload.id = id;
  if (!draft.enabled) payload.enabled = false;
  if (draft.connection.kind) payload.connection = connectionPayload(kinds, draft.connection);
  return payload;
}
