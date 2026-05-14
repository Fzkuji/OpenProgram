/**
 * FunctionForm — interactive parameter form rendered inside the
 * Composer when the user picks a program from the sidebar or welcome
 * screen. State lives in the parent Composer so the Send button (which
 * sits at the wrapper level, outside this component) can call submit
 * directly. Field rendering is delegated to ./fn-form-fields.
 */
"use client";

import { useCallback, useEffect } from "react";

import type { AgenticFunction, FnParam } from "@/lib/session-store";

import { FieldRow } from "./fn-form-fields";
import styles from "./fn-form.module.css";

const RUNTIME_PARAM_NAMES = new Set([
  "runtime",
  "callback",
  "exec_runtime",
  "review_runtime",
]);

export function visibleParams(fn: AgenticFunction): FnParam[] {
  return (fn.params_detail || []).filter(
    (p) => !RUNTIME_PARAM_NAMES.has(p.name) && !p.hidden,
  );
}

export function defaultParamValue(p: FnParam): string {
  const isBool = p.type === "bool" || p.type === "boolean";
  const def = (p.default ?? "").replace(/^["']|["']$/g, "");
  if (isBool) return def === "True" ? "True" : "False";
  if (p.options && p.options.length > 0 && def && p.options.includes(def)) {
    return def;
  }
  return "";
}

interface FunctionFormProps {
  fn: AgenticFunction;
  values: Record<string, string>;
  setValue: (name: string, v: string) => void;
  workdir: string;
  setWorkdir: (v: string) => void;
  errorParam: string | null;
  closing?: boolean;
  onClose: () => void;
  onSubmit: () => void;
}

export function FunctionForm({
  fn,
  values,
  setValue,
  workdir,
  setWorkdir,
  errorParam,
  closing,
  onClose,
  onSubmit,
}: FunctionFormProps) {
  const params = visibleParams(fn);
  const workdirMode = fn.workdir_mode ?? "optional";
  const showWorkdir = workdirMode !== "hidden";

  // The panel's height is its natural content height — no max-height
  // transition. The composer's wrapper height transition (driven from
  // Composer's `useLayoutEffect`) is what slides the form in/out from
  // behind the bottom row.

  // Workdir defaults — re-fetched whenever the function changes.
  // Server returns last-used path for this fn (+ home as a fallback for
  // the OS picker's start dir).
  useEffect(() => {
    let cancelled = false;
    const w = window as unknown as {
      currentSessionId?: string | null;
      _workdirHome?: string;
    };
    const query = new URLSearchParams();
    query.set("function_name", fn.name);
    if (w.currentSessionId) query.set("session_id", w.currentSessionId);
    fetch(`/api/workdir/defaults?${query.toString()}`)
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        if (data && typeof data.home === "string") {
          w._workdirHome = data.home;
        }
        if (data && data.last) setWorkdir(data.last);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fn.name]);

  const pickWorkdir = useCallback(async () => {
    const w = window as unknown as { _workdirHome?: string };
    const start = workdir.trim() || w._workdirHome || "";
    try {
      const r = await fetch("/api/pick-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start }),
      });
      const data = await r.json();
      if (r.ok && data.path) setWorkdir(data.path);
    } catch {
      /* ignore — user can still type a path */
    }
  }, [workdir, setWorkdir]);

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    const t = e.target as HTMLElement;
    const isTextarea = t.tagName === "TEXTAREA";
    if (e.key === "Enter") {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        onSubmit();
        return;
      }
      if (!e.shiftKey && !isTextarea) {
        e.preventDefault();
        onSubmit();
      }
    }
  }

  // Header / body / inputBottomRow are three flat siblings inside the
  // input wrapper. No `panel` div wraps body — body is itself a
  // direct wrapper child, mirroring how inputBottomRow is a direct
  // wrapper child at the other end. Nothing is nested inside another.
  return (
    <>
      <div
        data-fn-form-header
        className={`${styles.header} ${closing ? styles.closing : ""}`}
        onKeyDown={onKey}
      >
        <div
          className={styles.title}
          title={
            fn.description
              ? `function ${fn.name} – ${fn.description}`
              : `function ${fn.name}`
          }
        >
          <span className={styles.name}>
            <span className={styles.keyword}>function </span>
            {fn.name}
          </span>
          {fn.description ? (
            <>
              <span className={styles.dash}>{" – "}</span>
              <span className={styles.desc}>{fn.description}</span>
            </>
          ) : null}
        </div>
        {/* Close button now lives at the wrapper level (rendered by
            Composer) so it stays mounted across fn switches and
            doesn't blink. The header only carries title content. */}
      </div>
      <div
        data-fn-form-body
        className={`${styles.body} ${closing ? styles.closing : ""}`}
        onKeyDown={onKey}
      >
        {showWorkdir && (
          <WorkdirRow
            value={workdir}
            onChange={setWorkdir}
            onPick={pickWorkdir}
            error={errorParam === "__workdir"}
          />
        )}
        {params.length === 0 ? (
          <div className={styles.noParams}>
            No parameters needed — click run to execute
          </div>
        ) : (
          params.map((p) => (
            <FieldRow
              key={p.name}
              param={p}
              value={values[p.name] ?? ""}
              setValue={(v) => setValue(p.name, v)}
              error={errorParam === p.name}
            />
          ))
        )}
      </div>
    </>
  );
}

function WorkdirRow({
  value,
  onChange,
  onPick,
  error,
}: {
  value: string;
  onChange: (v: string) => void;
  onPick: () => void;
  error: boolean;
}) {
  return (
    <div className={styles.workdirRow}>
      <button
        type="button"
        className={styles.workdirBtn}
        onClick={onPick}
        title="Open folder chooser"
      >
        <FolderIcon />
        <span>Working in a folder</span>
      </button>
      <input
        type="text"
        id="fn-form-workdir"
        name="work_dir"
        className={`${styles.workdirInput} ${error ? styles.workdirError : ""}`}
        placeholder="/path/to/your/project"
        spellCheck={false}
        autoComplete="off"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function FolderIcon() {
  return (
    <svg
      viewBox="0 0 20 20"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M3 6a2 2 0 0 1 2-2h3l2 2h5a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  );
}
