"use client";

/** Chat-header model picker — dropdown grouped by provider. */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { useSessionStore } from "@/lib/session-store";
import { api } from "@/lib/api";

export function ModelBadge() {
  const providerInfo = useSessionStore((s) => s.providerInfo);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const { data: enabledModels } = useQuery({
    queryKey: ["models-enabled"],
    queryFn: api.listEnabledModels,
  });
  const [open, setOpen] = useState(false);

  const current = providerInfo
    ? `${providerInfo.provider ?? ""}/${providerInfo.model ?? ""}`
    : "—";

  async function pick(provider: string, model: string) {
    setOpen(false);
    try {
      // Pass session_id so the backend stamps provider_override /
      // model_override on THIS conversation. Without it the call
      // only nudges the global default and the active conv stays
      // bound to its previously-built runtime — that's the bug
      // where picking "Opus" silently still ran Sonnet.
      await api.switchModel(provider, model, currentSessionId || undefined);
    } catch (e) {
      alert("Switch failed: " + String(e));
    }
  }

  const byProvider = (enabledModels ?? []).reduce<Record<string, { id: string; name: string }[]>>(
    (acc, m) => {
      (acc[m.provider] ??= []).push({ id: m.id, name: m.name });
      return acc;
    },
    {}
  );

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 items-center gap-1 rounded-md px-2 text-[12px]"
        style={{ color: "var(--text-secondary)" }}
        onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
        onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
      >
        <span className="font-mono">{current}</span>
        <ChevronDown className="h-3 w-3" />
      </button>
      {open && (
        <div
          className="absolute left-0 top-full z-20 mt-1 max-h-[400px] w-[320px] overflow-y-auto rounded-md border py-1 shadow-lg"
          style={{
            background: "var(--bg-tertiary)",
            borderColor: "var(--border)",
          }}
        >
          {Object.keys(byProvider).length === 0 && (
            <div
              className="px-3 py-2 text-[12px]"
              style={{ color: "var(--text-muted)" }}
            >
              No enabled models. Go to Settings → LLM Providers.
            </div>
          )}
          {Object.entries(byProvider).map(([provider, models]) => (
            <div key={provider}>
              <div
                className="px-3 py-1 text-[10px] uppercase tracking-wide"
                style={{ color: "var(--text-muted)" }}
              >
                {provider}
              </div>
              {models.map((m) => (
                <button
                  key={m.id}
                  onClick={() => pick(provider, m.id)}
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] transition-colors"
                  style={{ color: "var(--text-primary)" }}
                  onMouseEnter={(e) =>
                    (e.currentTarget.style.background = "var(--bg-hover)")
                  }
                  onMouseLeave={(e) =>
                    (e.currentTarget.style.background = "transparent")
                  }
                >
                  <span className="flex-1 truncate">{m.name}</span>
                  <span
                    className="font-mono text-[10px]"
                    style={{ color: "var(--text-muted)" }}
                  >
                    {m.id}
                  </span>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
