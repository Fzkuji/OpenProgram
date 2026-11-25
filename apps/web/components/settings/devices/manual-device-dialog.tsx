"use client";

/**
 * Edit a device, or set one up by hand: its ID, name and its connection
 * (how it is reached), rendered from the connection method's settings
 * schema as the worker sends it. Add device offers the common cases in one
 * step each; this is the full form behind Edit and Add device → Set up by
 * hand.
 */
import { useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SecretInput } from "@/components/ui/secret-input";
import { SimpleSelect } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import {
  connectionDraft,
  draftFromMachine,
  draftPayload,
  draftProblems,
  emptyDraft,
  findKind,
  kindHint,
  kindLabel,
  kindOptions,
  settingFields,
  useMachines,
  withEnabled,
  type DraftProblem,
  type MachineDraft,
  type MachineInfo,
  type SettingField,
} from "@/lib/machines/store";
import styles from "../settings-page.module.css";
import { machineRequest, SAVE_WAIT_MS, type Text } from "./device-requests";


function problemText(problem: DraftProblem, draft: MachineDraft, text: Text): string {
  if (problem.field === "id") {
    const id = draft.id.trim();
    return problem.problem === "taken"
      ? text(`A device with the ID "${id}" already exists. Pick another ID.`, `ID 为“${id}”的设备已存在，请换一个 ID。`)
      : text("Give the device a name or an ID.", "请填写设备名称或 ID。");
  }
  if (problem.field === "connection") {
    return text("Choose how the device is reached.", "请选择怎样连接这台设备。");
  }
  const label = "label" in problem ? problem.label : problem.field;
  if (problem.problem === "required") return text(`${label} is required.`, `请填写${label}。`);
  if (problem.problem === "integer") return text(`${label} must be a whole number.`, `${label}必须是整数。`);
  if (problem.problem === "number") return text(`${label} must be a number.`, `${label}必须是数字。`);
  return text(`${label} is not in the expected form.`, `${label}的格式不对。`);
}

export function ManualDeviceDialog({ machine, onClose }: { machine: MachineInfo | null; onClose: () => void }) {
  const { text, locale } = useTranslation();
  const items = useMachines((s) => s.items);
  const kinds = useMachines((s) => s.kinds);
  const [draft, setDraft] = useState<MachineDraft>(() => (machine ? draftFromMachine(machine) : emptyDraft()));
  const [problems, setProblems] = useState<DraftProblem[]>([]);
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);

  function pickKind(kind: string) {
    setDraft((d) => ({ ...d, connection: connectionDraft(kinds, kind) }));
  }

  function setValue(key: string, value: string | boolean) {
    setDraft((d) => ({ ...d, connection: { ...d.connection, values: { ...d.connection.values, [key]: value } } }));
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const found = draftProblems(draft, kinds, items.map((item) => item.id), locale);
    setProblems(found);
    if (found.length) {
      setFormError(problemText(found[0], draft, text));
      return;
    }
    setFormError("");
    setBusy(true);
    const res = await machineRequest("save_machine", { machine: draftPayload(withEnabled(draft, items), kinds) }, text, SAVE_WAIT_MS);
    setBusy(false);
    if (!res.ok) {
      setFormError(res.error || "");
      return;
    }
    onClose();
  }

  const invalid = (path: string) => problems.some((problem) => problem.field === path) || undefined;
  const editingName = draft.name || draft.id;

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-[640px]">
        <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>
              {draft.editing ? text(`Edit "${editingName}"`, `编辑“${editingName}”`) : text("Set up a device by hand", "手动设置设备")}
            </DialogTitle>
          </DialogHeader>
          <div className={styles.authForm}>
            <label className={styles.authField}>
              {draft.editing ? text("ID", "ID") : text("ID (optional)", "ID（可选）")}
              <HoverTip label={draft.editing
                ? text("A device's ID never changes.", "设备的 ID 不能修改。")
                : text("Left blank, it is made from the name.", "留空时根据名称生成。")}>
                <div className="min-w-0">
                  <Input
                    placeholder="devbox"
                    spellCheck={false}
                    value={draft.id}
                    disabled={draft.editing}
                    aria-invalid={invalid("id")}
                    onChange={(e) => setDraft((d) => ({ ...d, id: e.target.value }))}
                  />
                </div>
              </HoverTip>
            </label>
            <label className={styles.authField}>
              {text("Name (optional)", "名称（可选）")}
              <Input value={draft.name} onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))} />
            </label>
            <ConnectionFields draft={draft} invalid={invalid} onPick={pickKind} onValue={setValue} />
          </div>
          {formError ? <p className={styles.rowStatusError}>{formError}</p> : null}
          <DialogFooter>
            <Button type="button" variant="elevated" onClick={onClose}>{text("Cancel", "取消")}</Button>
            <Button type="submit" disabled={busy}>{draft.editing ? text("Save", "保存") : text("Add", "添加")}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** The connection: its method picker, then that method's settings. */
function ConnectionFields({
  draft,
  invalid,
  onPick,
  onValue,
}: {
  draft: MachineDraft;
  invalid: (path: string) => true | undefined;
  onPick: (kind: string) => void;
  onValue: (key: string, value: string | boolean) => void;
}) {
  const { text, locale } = useTranslation();
  const kinds = useMachines((s) => s.kinds);
  const connection = draft.connection;
  const title = text("Connection", "连接方式");
  const offered = kindOptions(kinds);
  const current = findKind(kinds, connection.kind);
  const options = [
    ...offered.map((kind) => ({ value: kind.name, label: kindLabel(kind, locale) })),
    ...(connection.kind && !offered.some((kind) => kind.name === connection.kind)
      ? [{ value: connection.kind, label: current ? kindLabel(current, locale) : connection.kind }]
      : []),
  ];
  return (
    <>
      <label className={`${styles.authField} col-start-1`}>
        {title}
        <HoverTip label={kindHint(current, locale) || text("How OpenProgram reaches the device.", "OpenProgram 怎样连接这台设备。")}>
          <div className="min-w-0">
            <SimpleSelect
              label={title}
              className="max-w-none"
              value={connection.kind}
              placeholder={text("Choose…", "请选择…")}
              onValueChange={onPick}
              options={options}
            />
          </div>
        </HoverTip>
      </label>
      {settingFields(current, locale).map((field) => (
        <SettingControl
          key={field.key}
          field={field}
          value={connection.values[field.key]}
          held={connection.held.includes(field.key)}
          invalid={invalid(`connection.${field.key}`)}
          onChange={(value) => onValue(field.key, value)}
        />
      ))}
    </>
  );
}

/** One setting of a kind, as its schema describes it; the description is the control's hint. */
function SettingControl({
  field,
  value,
  held,
  invalid,
  onChange,
}: {
  field: SettingField;
  value: string | boolean | undefined;
  held: boolean;
  invalid: true | undefined;
  onChange: (value: string | boolean) => void;
}) {
  const { text } = useTranslation();
  const hinted = (control: ReactNode) => (field.hint
    ? <HoverTip label={field.hint}><div className="min-w-0">{control}</div></HoverTip>
    : control);
  if (field.type === "boolean") {
    return (
      <label className={`${styles.authField} justify-end`}>
        <span className="flex items-center gap-2">
          {hinted(<Switch aria-label={field.label} checked={value === true} onCheckedChange={(next) => onChange(next)} />)}
          {field.label}
        </span>
      </label>
    );
  }
  const textValue = typeof value === "string" ? value : "";
  let control: ReactNode;
  if (field.type === "choice") {
    control = (
      <SimpleSelect
        label={field.label}
        className="max-w-none"
        value={textValue}
        onValueChange={onChange}
        options={[
          ...(field.required ? [] : [{ value: "", label: text("None", "无") }]),
          ...field.choices.map((choice) => ({ value: choice, label: choice })),
        ]}
      />
    );
  } else if (field.secret) {
    control = (
      <SecretInput
        placeholder={held ? text("Saved. Type to replace it.", "已保存，输入新值即可替换。") : field.placeholder}
        value={textValue}
        aria-invalid={invalid}
        required={field.required && !held}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  } else {
    control = (
      <Input
        placeholder={field.placeholder}
        spellCheck={false}
        inputMode={field.type === "text" ? undefined : "decimal"}
        className={styles.authMono}
        value={textValue}
        aria-invalid={invalid}
        required={field.required}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  return (
    <label className={styles.authField}>
      {field.required ? field.label : text(`${field.label} (optional)`, `${field.label}（可选）`)}
      {hinted(control)}
    </label>
  );
}
