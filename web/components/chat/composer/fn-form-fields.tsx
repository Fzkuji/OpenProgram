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

  const placeholder = (() => {
    if (p.placeholder) return p.placeholder;
    if (defaultVal && defaultVal !== "None" && !defaultVal.startsWith("_")) {
      return `default: ${defaultVal}`;
    }
    return "";
  })();

  const errorClass = error ? styles.inputError : "";
  const inputClass = `${styles.input} ${errorClass}`.trim();

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
  return (
    <div className={styles.label}>
      <span className={styles.labelName}>{p.label ?? p.name}</span>
      {p.type ? <span className={styles.labelType}>{p.type}</span> : null}
      {p.required ? (
        <span className={styles.labelRequired}>*</span>
      ) : (
        <span className={styles.labelOptional}>optional</span>
      )}
      {p.description ? (
        <span className={styles.labelDesc}>{p.description}</span>
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
}: {
  id?: string;
  name?: string;
  className: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
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
    />
  );
}
