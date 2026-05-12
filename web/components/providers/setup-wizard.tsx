"use client";

import { useEffect, useState } from "react";
import { X, Check, AlertCircle, HelpCircle, ArrowRight, Loader2 } from "lucide-react";

interface StepResult {
  status: "ok" | "error" | "needs_input";
  message?: string;
  fix?: string;
  options?: { value: string; desc?: string }[];
  default?: string;
  input_key?: string;
}

interface WizardSchema {
  label: string;
  description?: string;
  steps: { id: string; label: string }[];
}

interface Props {
  providerId: string;
  onClose: () => void;
}

export function SetupWizard({ providerId, onClose }: Props) {
  const [schema, setSchema] = useState<WizardSchema | null>(null);
  const [ctx, setCtx] = useState<Record<string, string>>({});
  const [results, setResults] = useState<(StepResult | null)[]>([]);
  const [idx, setIdx] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`/api/providers/${providerId}/configure`);
        if (!r.ok) {
          setError(`No configuration wizard for ${providerId}`);
          return;
        }
        const s = (await r.json()) as WizardSchema;
        setSchema(s);
        setResults(new Array(s.steps.length).fill(null));
      } catch (e) {
        setError(String(e));
      }
    })();
  }, [providerId]);

  useEffect(() => {
    if (!schema || error || results[idx] !== null || running || idx >= schema.steps.length) return;
    runStep();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schema, idx, results]);

  async function runStep() {
    if (!schema) return;
    const step = schema.steps[idx];
    if (!step) return;
    setRunning(true);
    try {
      const r = await fetch(
        `/api/providers/${providerId}/configure/step/${encodeURIComponent(step.id)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(ctx),
        }
      );
      const d = await r.json();
      const newResults = [...results];
      newResults[idx] = d.result as StepResult;
      setResults(newResults);
      if (d.context) setCtx(d.context);
      if (d.result.status === "ok") setIdx(idx + 1);
    } catch (e) {
      const newResults = [...results];
      newResults[idx] = {
        status: "error",
        message: "Network error: " + String(e),
      };
      setResults(newResults);
    } finally {
      setRunning(false);
    }
  }

  function submitInput(key: string, value: string) {
    if (!value) {
      alert("Please pick a value");
      return;
    }
    setCtx({ ...ctx, [key]: value });
    const newResults = [...results];
    newResults[idx] = null;
    setResults(newResults);
  }

  function retry() {
    const newResults = [...results];
    newResults[idx] = null;
    setResults(newResults);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.5)" }}
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-xl flex-col overflow-hidden rounded-lg border"
        style={{
          background: "var(--bg-secondary)",
          borderColor: "var(--border)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="flex items-center justify-between border-b px-5 py-3"
          style={{ borderColor: "var(--border)" }}
        >
          <h2
            className="text-[15px] font-semibold"
            style={{ color: "var(--text-bright)" }}
          >
            Setup: {schema?.label ?? providerId}
          </h2>
          <button onClick={onClose}>
            <X className="h-4 w-4" style={{ color: "var(--text-muted)" }} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          {error ? (
            <div className="text-[13px]" style={{ color: "var(--accent-red)" }}>
              {error}
            </div>
          ) : !schema ? (
            <div
              className="flex items-center gap-2 text-[13px]"
              style={{ color: "var(--text-muted)" }}
            >
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading…
            </div>
          ) : (
            <>
              {schema.description && (
                <p
                  className="mb-4 text-[12px]"
                  style={{ color: "var(--text-muted)" }}
                >
                  {schema.description}
                </p>
              )}
              <div className="space-y-0">
                {schema.steps.map((step, i) => (
                  <StepRow
                    key={step.id}
                    step={step}
                    result={results[i]}
                    active={i === idx}
                    running={running && i === idx}
                    onSubmitInput={submitInput}
                    onRetry={retry}
                    onClose={onClose}
                  />
                ))}
              </div>
              {idx >= schema.steps.length && (
                <div
                  className="mt-4 rounded-md p-3 text-[13px]"
                  style={{
                    background: "var(--bg-tertiary)",
                    color: "var(--accent-green)",
                  }}
                >
                  ✓ All steps complete. Provider configured.
                </div>
              )}
            </>
          )}
        </div>

        <div
          className="flex justify-end border-t px-5 py-3"
          style={{ borderColor: "var(--border)" }}
        >
          <button
            onClick={onClose}
            className="h-8 rounded-md border px-3 text-[13px]"
            style={{
              background: "transparent",
              borderColor: "var(--border)",
              color: "var(--text-primary)",
            }}
          >
            {idx >= (schema?.steps.length ?? 0) ? "Done" : "Cancel"}
          </button>
        </div>
      </div>
    </div>
  );
}

function StepRow({
  step,
  result,
  active,
  running,
  onSubmitInput,
  onRetry,
  onClose,
}: {
  step: { id: string; label: string };
  result: StepResult | null;
  active: boolean;
  running: boolean;
  onSubmitInput: (key: string, value: string) => void;
  onRetry: () => void;
  onClose: () => void;
}) {
  let Icon = ArrowRight;
  let color = "var(--text-muted)";
  if (running) {
    Icon = Loader2;
    color = "var(--accent-blue)";
  } else if (result) {
    if (result.status === "ok") {
      Icon = Check;
      color = "var(--accent-green)";
    } else if (result.status === "error") {
      Icon = AlertCircle;
      color = "var(--accent-red)";
    } else if (result.status === "needs_input") {
      Icon = HelpCircle;
      color = "var(--accent-blue)";
    }
  } else if (active) {
    Icon = ArrowRight;
    color = "var(--accent-blue)";
  }

  return (
    <div
      className="flex gap-3 border-b py-3 last:border-b-0"
      style={{ borderColor: "var(--border)" }}
    >
      <Icon
        className={`h-4 w-4 shrink-0 mt-0.5 ${running ? "animate-spin" : ""}`}
        style={{ color }}
      />
      <div className="min-w-0 flex-1">
        <div
          className="text-[13px] font-medium"
          style={{ color: "var(--text-bright)" }}
        >
          {step.label}
        </div>
        {result?.message && (
          <div
            className="mt-1 text-[12px]"
            style={{ color: "var(--text-muted)" }}
          >
            {result.message}
          </div>
        )}
        {result?.status === "error" && result.fix && (
          <>
            <div
              className="mt-1 text-[11px]"
              style={{ color: "var(--text-muted)" }}
            >
              Fix:{" "}
              <code
                className="rounded px-1"
                style={{ background: "var(--bg-tertiary)" }}
              >
                {result.fix}
              </code>
            </div>
            <div className="mt-2 flex gap-2">
              <button
                onClick={onRetry}
                className="h-7 rounded-md border px-3 text-[12px]"
                style={{
                  borderColor: "var(--border)",
                  color: "var(--text-primary)",
                }}
              >
                Retry
              </button>
              <button
                onClick={onClose}
                className="h-7 rounded-md border px-3 text-[12px]"
                style={{
                  borderColor: "var(--border)",
                  color: "var(--text-primary)",
                }}
              >
                Close
              </button>
            </div>
          </>
        )}
        {result?.status === "needs_input" && result.input_key && (
          <NeedsInputForm
            result={result}
            onSubmit={(v) => onSubmitInput(result.input_key!, v)}
          />
        )}
      </div>
    </div>
  );
}

function NeedsInputForm({
  result,
  onSubmit,
}: {
  result: StepResult;
  onSubmit: (v: string) => void;
}) {
  const [value, setValue] = useState(result.default ?? "");
  const options = result.options ?? [];

  return (
    <div className="mt-2 space-y-2">
      {options.length > 0 ? (
        <div className="space-y-1">
          {options.map((o) => (
            <label
              key={o.value}
              className="flex cursor-pointer items-center gap-2 rounded-md border p-2 text-[12px]"
              style={{
                borderColor: value === o.value ? "var(--accent-blue)" : "var(--border)",
                background: value === o.value ? "var(--bg-tertiary)" : "transparent",
              }}
            >
              <input
                type="radio"
                checked={value === o.value}
                onChange={() => setValue(o.value)}
              />
              <span style={{ color: "var(--text-primary)" }}>{o.value}</span>
              {o.desc && (
                <span style={{ color: "var(--text-muted)" }}>— {o.desc}</span>
              )}
            </label>
          ))}
        </div>
      ) : (
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={result.default ?? ""}
          className="h-8 w-full rounded-md border px-2 text-[12px]"
          style={{
            background: "var(--bg-input)",
            borderColor: "var(--border)",
            color: "var(--text-primary)",
          }}
        />
      )}
      <button
        onClick={() => onSubmit(value)}
        className="h-7 rounded-md px-3 text-[12px] text-white"
        style={{ background: "var(--accent-blue)" }}
      >
        Continue
      </button>
    </div>
  );
}
