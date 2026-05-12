"use client";

import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import type { Provider } from "@/lib/types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface Props {
  providers: Provider[];
  activeName: string | null;
  onSelect: (name: string) => void;
}

export function ProviderList({ providers, activeName, onSelect }: Props) {
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.toggleProvider(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });

  return (
    <aside
      className="overflow-hidden rounded-[10px] border"
      style={{
        background: "var(--bg-secondary)",
        borderColor: "var(--border)",
      }}
    >
      <div
        className="border-b px-4 py-3"
        style={{ borderColor: "var(--border)" }}
      >
        <h3
          className="text-[13px] font-semibold"
          style={{ color: "var(--text-bright)" }}
        >
          Providers
        </h3>
        <p className="mt-0.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
          {providers.filter((p) => p.enabled).length} of {providers.length} enabled
        </p>
      </div>
      <ul className="max-h-[calc(100vh-260px)] overflow-y-auto p-1">
        {providers.map((p) => {
          const active = activeName === p.id;
          return (
            <li key={p.id}>
              <button
                onClick={() => onSelect(p.id)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-[13px] transition-colors"
                )}
                style={{
                  background: active ? "var(--bg-tertiary)" : "transparent",
                  color: active ? "var(--text-bright)" : "var(--text-primary)",
                }}
                onMouseEnter={(e) => {
                  if (!active)
                    e.currentTarget.style.background = "var(--bg-hover)";
                }}
                onMouseLeave={(e) => {
                  if (!active) e.currentTarget.style.background = "transparent";
                }}
              >
                <span className="flex-1 truncate">{p.label}</span>
                {p.configured && p.enabled_model_count > 0 && (
                  <span
                    className="rounded-sm px-1.5 text-[10px]"
                    style={{
                      background: "var(--bg-hover-contrast)",
                      color: "var(--text-secondary)",
                    }}
                  >
                    {p.enabled_model_count}
                  </span>
                )}
                <Switch
                  checked={p.enabled}
                  onClick={(e) => e.stopPropagation()}
                  onCheckedChange={(enabled) =>
                    toggle.mutate({ id: p.id, enabled })
                  }
                  className="scale-[0.75]"
                />
              </button>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
