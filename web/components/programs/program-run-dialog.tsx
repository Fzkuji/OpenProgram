"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { X, Play, Eye, Loader2, Folder } from "lucide-react";
import type { AgenticFunction, FunctionParamDetail } from "@/lib/types";
import { api } from "@/lib/api";

interface Props {
  fn: AgenticFunction;
  onClose: () => void;
  onRun?: (name: string) => void;
}

export function ProgramRunDialog({ fn, onClose }: Props) {
  const router = useRouter();
  const visible = fn.params_detail.filter((p) => !p.hidden);
  const [values, setValues] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const p of visible) {
      init[p.name] =
        p.default && p.default !== "None" ? String(p.default) : "";
    }
    return init;
  });
  const [result, setResult] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  function runInChat() {
    // Build /run command text
    const parts: string[] = [];
    for (const p of visible) {
      const v = values[p.name];
      if (!v && !p.required) continue;
      parts.push(`${p.name}=${JSON.stringify(v)}`);
    }
    const cmd = `/run ${fn.name} ${parts.join(" ")}`.trim();
    router.push(`/chat?prefill=${encodeURIComponent(cmd)}`);
    onClose();
  }

  async function runInline() {
    setRunning(true);
    setResult(null);
    try {
      const payload: Record<string, unknown> = {};
      for (const p of visible) {
        const v = values[p.name];
        if (v === "" && !p.required) continue;
        if (p.type === "bool") payload[p.name] = v === "true";
        else if (p.type === "int") payload[p.name] = Number(v);
        else if (p.type === "float") payload[p.name] = Number(v);
        else payload[p.name] = v;
      }
      const r = await api.runFunction(fn.name, payload);
      setResult(JSON.stringify(r.result ?? r.error ?? r, null, 2));
    } catch (e) {
      setResult(String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.5)" }}
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg border"
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
          <div>
            <h2
              className="font-mono text-[15px] font-semibold"
              style={{ color: "var(--text-bright)" }}
            >
              {fn.name}
            </h2>
            <p className="mt-0.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
              {fn.description}
            </p>
          </div>
          <button onClick={onClose}>
            <X className="h-4 w-4" style={{ color: "var(--text-muted)" }} />
          </button>
        </div>

        <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
          {visible.length === 0 ? (
            <p className="text-[13px]" style={{ color: "var(--text-muted)" }}>
              No parameters. Click Run to execute.
            </p>
          ) : (
            visible.map((p) => (
              <ParamInput
                key={p.name}
                p={p}
                value={values[p.name] ?? ""}
                onChange={(v) => setValues({ ...values, [p.name]: v })}
              />
            ))
          )}
          {result !== null && (
            <div className="mt-4">
              <h3
                className="mb-1 text-[11px] font-semibold uppercase tracking-wide"
                style={{ color: "var(--text-secondary)" }}
              >
                Result
              </h3>
              <pre
                className="max-h-64 overflow-auto rounded-md border p-3 font-mono text-[11px]"
                style={{
                  background: "var(--bg-input)",
                  borderColor: "var(--border)",
                  color: "var(--text-primary)",
                }}
              >
                {result}
              </pre>
            </div>
          )}
        </div>

        <div
          className="flex justify-end gap-2 border-t px-5 py-3"
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
            Cancel
          </button>
          <button
            onClick={runInline}
            disabled={running}
            className="flex h-8 items-center gap-1 rounded-md border px-3 text-[13px]"
            style={{
              background: "transparent",
              borderColor: "var(--border)",
              color: "var(--text-primary)",
            }}
          >
            {running ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Eye className="h-3.5 w-3.5" />
            )}
            Run inline
          </button>
          <button
            onClick={runInChat}
            className="flex h-8 items-center gap-1 rounded-md px-3 text-[13px] text-white"
            style={{ background: "var(--accent-blue)" }}
          >
            <Play className="h-3.5 w-3.5" />
            Run in chat
          </button>
        </div>
      </div>
    </div>
  );
}

function ParamInput({
  p,
  value,
  onChange,
}: {
  p: FunctionParamDetail;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center gap-2">
        <label
          className="font-mono text-[12px] font-medium"
          style={{ color: "var(--text-bright)" }}
        >
          {p.name}
        </label>
        <span
          className="font-mono text-[10px]"
          style={{ color: "var(--text-muted)" }}
        >
          {p.type}
        </span>
        {p.required && (
          <span
            className="text-[10px]"
            style={{ color: "var(--accent-red)" }}
          >
            required
          </span>
        )}
      </div>
      {p.description && (
        <p className="mb-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
          {p.description}
        </p>
      )}
      {p.type === "bool" ? (
        <select
          value={value || "false"}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 w-full rounded-md border px-2 text-[12px]"
          style={{
            background: "var(--bg-input)",
            borderColor: "var(--border)",
            color: "var(--text-primary)",
          }}
        >
          <option value="false">false</option>
          <option value="true">true</option>
        </select>
      ) : p.choices && p.choices.length > 0 ? (
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 w-full rounded-md border px-2 text-[12px]"
          style={{
            background: "var(--bg-input)",
            borderColor: "var(--border)",
            color: "var(--text-primary)",
          }}
        >
          {p.choices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      ) : p.multiline ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={p.placeholder}
          rows={3}
          className="w-full resize-y rounded-md border p-2 text-[13px]"
          style={{
            background: "var(--bg-input)",
            borderColor: "var(--border)",
            color: "var(--text-primary)",
          }}
        />
      ) : /(Path|folder|dir|directory|workdir)/i.test(p.type) ||
        /(workdir|folder|directory|dir_path|work_dir)/i.test(p.name) ? (
        <div className="flex gap-2">
          <input
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={p.placeholder ?? "/path/to/folder"}
            className="h-8 flex-1 rounded-md border px-2 font-mono text-[12px]"
            style={{
              background: "var(--bg-input)",
              borderColor: "var(--border)",
              color: "var(--text-primary)",
            }}
          />
          <button
            type="button"
            onClick={async () => {
              try {
                const r = await fetch("/api/pick-folder", { method: "POST" });
                const j = await r.json();
                if (j.path) onChange(j.path);
              } catch (e) {
                alert("Folder picker unavailable: " + String(e));
              }
            }}
            className="flex h-8 shrink-0 items-center gap-1 rounded-md border px-2 text-[12px]"
            style={{
              background: "transparent",
              borderColor: "var(--border)",
              color: "var(--text-primary)",
            }}
          >
            <Folder className="h-3.5 w-3.5" />
            Browse
          </button>
        </div>
      ) : (
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={p.placeholder}
          className="h-8 w-full rounded-md border px-2 text-[13px]"
          style={{
            background: "var(--bg-input)",
            borderColor: "var(--border)",
            color: "var(--text-primary)",
          }}
        />
      )}
    </div>
  );
}
