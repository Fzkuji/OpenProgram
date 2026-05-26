"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Play } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import type { AgenticFunction, FunctionParam } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  fn: AgenticFunction;
  onClose: () => void;
}

export function ProgramDialog({ fn, onClose }: Props) {
  const router = useRouter();
  const visible = useMemo(
    () => fn.params_detail.filter((p) => !p.hidden),
    [fn],
  );
  const [values, setValues] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const p of visible) init[p.name] = p.default ?? "";
    return init;
  });
  const [error, setError] = useState<string | null>(null);

  function buildKwargs(): Record<string, unknown> {
    const kwargs: Record<string, unknown> = {};
    for (const p of visible) {
      const v = values[p.name];
      if (v === "" || v === undefined) continue;
      if (p.type === "bool" || p.type === "boolean") {
        kwargs[p.name] = v === "true" || v === "True" || v === "1";
      } else if (p.type === "int") {
        const n = parseInt(v, 10);
        kwargs[p.name] = Number.isFinite(n) ? n : v;
      } else if (p.type === "float" || p.type === "number") {
        const n = parseFloat(v);
        kwargs[p.name] = Number.isFinite(n) ? n : v;
      } else {
        kwargs[p.name] = v;
      }
    }
    return kwargs;
  }

  async function submit() {
    const missing = visible.filter(
      (p) => p.required && (!values[p.name] || values[p.name].trim() === ""),
    );
    if (missing.length > 0) {
      setError(`Missing required: ${missing.map((p) => p.name).join(", ")}`);
      return;
    }
    const curSession = (window as unknown as { currentSessionId?: string | null })
      .currentSessionId;
    const body: Record<string, unknown> = { kwargs: buildKwargs() };
    if (curSession) body.session_id = curSession;
    try {
      await fetch(`/api/function/${encodeURIComponent(fn.name)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (e) {
      setError(String(e));
      return;
    }
    router.push(curSession ? `/s/${curSession}` : "/chat");
    onClose();
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <div className="flex items-center gap-2 pr-6">
            <DialogTitle className="font-mono">{fn.name}</DialogTitle>
            <Badge variant="outline">{fn.category || "—"}</Badge>
          </div>
          {fn.description && (
            <DialogDescription>{fn.description}</DialogDescription>
          )}
        </DialogHeader>

        <div className="scroll-y max-h-[60vh] flex-1 px-5 py-4">
          {visible.length === 0 ? (
            <p className="text-sm text-(--fg-muted)">No parameters.</p>
          ) : (
            <div className="flex flex-col gap-4">
              {visible.map((p) => (
                <ParamField
                  key={p.name}
                  param={p}
                  value={values[p.name] ?? ""}
                  onChange={(v) =>
                    setValues((prev) => ({ ...prev, [p.name]: v }))
                  }
                />
              ))}
            </div>
          )}
          {error && (
            <div className="mt-3 rounded-md border border-(--danger)/40 bg-(--danger)/10 px-3 py-2 text-xs text-(--danger)">
              {error}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit}>
            <Play size={13} />
            Run
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ParamField({
  param,
  value,
  onChange,
}: {
  param: FunctionParam;
  value: string;
  onChange: (v: string) => void;
}) {
  const label = (
    <label className="flex items-center gap-1.5 text-xs">
      <span className="font-mono font-medium text-(--fg)">{param.name}</span>
      <span className="text-(--fg-subtle)">: {param.type}</span>
      {param.required && <span className="text-(--danger)">*</span>}
    </label>
  );

  if (param.type === "bool" || param.type === "boolean") {
    const checked =
      value === "true" || value === "True" || value === "1" || value === "yes";
    return (
      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between">
          {label}
          <button
            onClick={() => onChange(checked ? "false" : "true")}
            className={cn(
              "relative h-5 w-9 rounded-full border transition-colors",
              checked
                ? "border-(--accent) bg-(--accent)"
                : "border-(--border) bg-(--bg-elevated)",
            )}
            aria-pressed={checked}
          >
            <span
              className={cn(
                "absolute top-0.5 inline-block h-4 w-4 rounded-full bg-white shadow-(--shadow-sm) transition-transform",
                checked ? "translate-x-[18px]" : "translate-x-0.5",
              )}
            />
          </button>
        </div>
        {param.description && <Help>{param.description}</Help>}
      </div>
    );
  }

  if (param.choices && param.choices.length > 0) {
    return (
      <div className="flex flex-col gap-1">
        {label}
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 w-full rounded-md border border-(--border) bg-(--bg-input) px-2 text-sm text-(--fg) focus:outline-none focus:ring-2 focus:ring-(--ring)"
        >
          {!param.required && <option value="">—</option>}
          {param.choices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        {param.description && <Help>{param.description}</Help>}
      </div>
    );
  }

  if (param.multiline) {
    return (
      <div className="flex flex-col gap-1">
        {label}
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={param.placeholder}
          rows={4}
          className="w-full resize-y rounded-md border border-(--border) bg-(--bg-input) px-2 py-1.5 text-sm text-(--fg) placeholder:text-(--fg-subtle) focus:outline-none focus:ring-2 focus:ring-(--ring)"
        />
        {param.description && <Help>{param.description}</Help>}
      </div>
    );
  }

  const isNumber = ["int", "float", "number"].includes(param.type);
  return (
    <div className="flex flex-col gap-1">
      {label}
      <Input
        type={isNumber ? "number" : "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={param.placeholder}
      />
      {param.description && <Help>{param.description}</Help>}
    </div>
  );
}

function Help({ children }: { children: React.ReactNode }) {
  return <p className="text-[11px] text-(--fg-subtle)">{children}</p>;
}
