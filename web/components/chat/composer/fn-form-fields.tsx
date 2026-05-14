/**
 * Field renderers for the function parameter form. One row per visible
 * param, dispatching by type to the right input element (bool toggle,
 * function-select dropdown, option chips, textarea, plain input).
 */
"use client";

import { useEffect, useRef, type ReactNode } from "react";

import type { FnParam } from "@/lib/session-store";

import styles from "./fn-form.module.css";

function stripQuotes(s: string): string {
  return s.replace(/^["']|["']$/g, "");
}

export function FieldRow({
  param: p,
  value,
  setValue,
  error,
}: {
  param: FnParam;
  value: string;
  setValue: (v: string) => void;
  error: boolean;
}) {
  const defaultVal = stripQuotes(p.default ?? "");
  const isBool = p.type === "bool" || p.type === "boolean";
  const isMultiline =
    p.multiline !== undefined
      ? p.multiline
      : !isBool && (p.type === "str" || p.type === "string" || !p.type);

  // Placeholder = raw default value (no "default:" prefix). It already
  // reads as ghost text inside the field — labelling it as "default" a
  // second time would be redundant noise; pressing Tab promotes the
  // ghost into the actual value.
  const placeholder = (() => {
    if (p.placeholder) return p.placeholder;
    if (defaultVal && defaultVal !== "None" && !defaultVal.startsWith("_")) {
      return defaultVal;
    }
    return "";
  })();

  const errorClass = error ? styles.inputError : "";
  const inputClass = `${styles.input} ${errorClass}`.trim();

  // Tab autocomplete: when the field is empty and a default exists,
  // Tab inserts the default as the real value (rather than moving
  // focus). The placeholder ghost text becomes the actual content.
  const ghost = defaultVal && defaultVal !== "None" ? defaultVal : "";
  const onAutocompleteKey = (
    e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => {
    if (e.key !== "Tab" || e.shiftKey) return;
    if (value !== "" || !ghost) return;
    e.preventDefault();
    setValue(ghost);
  };

  const fieldId = `fn-field-${p.name}`;
  let node: ReactNode;
  if (isBool) {
    node = <BoolToggle value={value} onChange={setValue} />;
  } else if (p.options_from === "functions") {
    node = (
      <FunctionSelect
        id={fieldId}
        name={p.name}
        className={`${inputClass} ${styles.select}`}
        value={value}
        onChange={setValue}
      />
    );
  } else if (p.options && p.options.length > 0) {
    node = (
      <OptionChips
        name={p.name}
        options={p.options}
        value={value}
        onChange={setValue}
      />
    );
  } else if (isMultiline) {
    node = (
      <AutoTextarea
        id={fieldId}
        name={p.name}
        className={`${inputClass} ${styles.textarea}`}
        placeholder={placeholder}
        value={value}
        onChange={setValue}
        onKeyDown={onAutocompleteKey}
      />
    );
  } else {
    node = (
      <input
        id={fieldId}
        name={p.name}
        autoComplete="off"
        className={inputClass}
        placeholder={placeholder}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onAutocompleteKey}
      />
    );
  }

  return (
    <div className={styles.field}>
      <FieldLabel p={p} />
      {node}
    </div>
  );
}

function FieldLabel({ p }: { p: FnParam }) {
  // Mirrors the PyTorch-docs convention:
  //   param_name (type, optional) – description.
  // The default value isn't repeated here — the input below already
  // shows it as placeholder ghost text, and Tab autocompletes it into
  // the real value.
  const meta = [p.type || "any", p.required ? null : "optional"]
    .filter(Boolean)
    .join(", ");
  // Full description goes into a native `title` tooltip — the visible
  // line is mask-faded on overflow, hovering shows the full text via
  // the browser's own tooltip overlay (no inline layout shift).
  const tooltip = p.description
    ? `${p.label ?? p.name} (${meta}) – ${p.description}`
    : `${p.label ?? p.name} (${meta})`;
  return (
    <div className={styles.label} title={tooltip}>
      <span className={styles.labelName}>{p.label ?? p.name}</span>
      <span className={styles.labelMeta}>{` (${meta})`}</span>
      {p.description ? (
        <>
          <span className={styles.labelDash}>{" – "}</span>
          <span className={styles.labelDesc}>{p.description}</span>
        </>
      ) : null}
    </div>
  );
}

function BoolToggle({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const yes = value === "True";
  return (
    <div className={styles.toggle}>
      <button
        type="button"
        className={`${styles.toggleBtn} ${yes ? styles.toggleActive : ""}`}
        onClick={() => onChange("True")}
      >
        Yes
      </button>
      <button
        type="button"
        className={`${styles.toggleBtn} ${!yes ? styles.toggleActive : ""}`}
        onClick={() => onChange("False")}
      >
        No
      </button>
    </div>
  );
}

function FunctionSelect({
  id,
  name,
  className,
  value,
  onChange,
}: {
  id?: string;
  name?: string;
  className: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const w = window as unknown as {
    availableFunctions?: { name: string; category?: string }[];
  };
  const fns = (w.availableFunctions ?? []).filter((f) => {
    const cat = f.category || "user";
    return cat !== "meta" && cat !== "builtin";
  });
  return (
    <select
      id={id}
      name={name}
      autoComplete="off"
      className={className}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">-- select --</option>
      {fns.map((f) => (
        <option key={f.name} value={f.name}>
          {f.name}
        </option>
      ))}
    </select>
  );
}

function OptionChips({
  name,
  options,
  value,
  onChange,
}: {
  name?: string;
  options: string[];
  value: string;
  onChange: (v: string) => void;
}) {
  const knownMatch = options.includes(value);
  return (
    <div className={styles.options}>
      {options.map((opt) => (
        <button
          key={opt}
          type="button"
          className={`${styles.chip} ${value === opt ? styles.chipActive : ""}`}
          onClick={() => onChange(opt)}
        >
          {opt}
        </button>
      ))}
      <input
        type="text"
        name={name ? `${name}_custom` : undefined}
        autoComplete="off"
        className={styles.chipCustom}
        placeholder="..."
        value={knownMatch ? "" : value}
        onChange={(e) => onChange(e.target.value.trim())}
      />
    </div>
  );
}

function AutoTextarea({
  id,
  name,
  className,
  placeholder,
  value,
  onChange,
  onKeyDown,
}: {
  id?: string;
  name?: string;
  className: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  onKeyDown?: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const t = ref.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = `${Math.min(t.scrollHeight, 160)}px`;
  }, [value]);
  return (
    <textarea
      ref={ref}
      id={id}
      name={name}
      autoComplete="off"
      className={className}
      rows={2}
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={onKeyDown}
    />
  );
}
