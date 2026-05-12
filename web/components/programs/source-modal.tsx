"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { X, Save, Loader2, FileCode } from "lucide-react";
import type { AgenticFunction } from "@/lib/types";
import { api } from "@/lib/api";

interface Props {
  fn: AgenticFunction;
  onClose: () => void;
}

export function SourceModal({ fn, onClose }: Props) {
  const qc = useQueryClient();
  const [edited, setEdited] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const { data } = useQuery({
    queryKey: ["source", fn.name],
    queryFn: () => api.getFunctionSource(fn.name),
  });

  const display = edited ?? data?.source ?? "";
  const dirty = edited !== null && edited !== (data?.source ?? "");

  async function save() {
    if (!dirty) return;
    setSaving(true);
    try {
      const r = await fetch(
        `/api/function/${encodeURIComponent(fn.name)}/edit`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source: edited }),
        }
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      qc.invalidateQueries({ queryKey: ["source", fn.name] });
      qc.invalidateQueries({ queryKey: ["functions"] });
      setEdited(null);
    } catch (e) {
      alert("Save failed: " + String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.5)" }}
      onClick={onClose}
    >
      <div
        className="flex h-[85vh] w-full max-w-4xl flex-col overflow-hidden rounded-lg border"
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
          <div className="flex items-center gap-2">
            <FileCode
              className="h-4 w-4"
              style={{ color: "var(--text-muted)" }}
            />
            <h2
              className="font-mono text-[14px] font-semibold"
              style={{ color: "var(--text-bright)" }}
            >
              {fn.name}
            </h2>
            {data?.filepath && (
              <span
                className="font-mono text-[10px]"
                style={{ color: "var(--text-muted)" }}
              >
                {data.filepath.replace(/^.*\/openprogram\//, "openprogram/")}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={save}
              disabled={!dirty || saving}
              className="flex h-7 items-center gap-1 rounded-md px-3 text-[12px] text-white disabled:opacity-50"
              style={{ background: "var(--accent-blue)" }}
            >
              {saving ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Save className="h-3 w-3" />
              )}
              Save
            </button>
            <button onClick={onClose}>
              <X className="h-4 w-4" style={{ color: "var(--text-muted)" }} />
            </button>
          </div>
        </div>

        <textarea
          value={display}
          onChange={(e) => setEdited(e.target.value)}
          spellCheck={false}
          className="flex-1 resize-none p-4 font-mono text-[12px] outline-none"
          style={{
            background: "var(--bg-input)",
            color: "var(--text-primary)",
            lineHeight: 1.55,
          }}
        />
      </div>
    </div>
  );
}
