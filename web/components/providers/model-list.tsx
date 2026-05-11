"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, useMemo } from "react";
import { Search, Eye, Video, Wrench, Brain, FileText, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { Provider, Capability } from "@/lib/types";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

const capConfig: Record<
  Capability,
  { icon: typeof Eye; color: string; label: string }
> = {
  vision: { icon: Eye, color: "var(--accent-cyan)", label: "Vision" },
  video: { icon: Video, color: "var(--accent-blue)", label: "Video" },
  tools: { icon: Wrench, color: "var(--accent-green)", label: "Tools" },
  reasoning: {
    icon: Brain,
    color: "var(--accent-purple)",
    label: "Reasoning",
  },
  ctx: { icon: FileText, color: "var(--text-muted)", label: "Context" },
};

function CapBadge({ cap, context }: { cap: Capability; context?: number }) {
  const { icon: Icon, color, label } = capConfig[cap];
  const text =
    cap === "ctx" && context
      ? context >= 1000
        ? `${Math.round(context / 1000)}k`
        : `${context}`
      : label;
  return (
    <span
      className="inline-flex items-center gap-1 text-[10px]"
      style={{ color }}
    >
      <Icon className="h-3 w-3" />
      {text}
    </span>
  );
}

export function ModelList({ provider }: { provider: Provider }) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");

  const { data: models, isLoading } = useQuery({
    queryKey: ["models", provider.id],
    queryFn: () => api.listModels(provider.id),
  });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.toggleModel(provider.id, id, enabled),
    onMutate: async ({ id, enabled }) => {
      await qc.cancelQueries({ queryKey: ["models", provider.id] });
      const prev = qc.getQueryData(["models", provider.id]);
      qc.setQueryData(["models", provider.id], (old: typeof models) =>
        old?.map((m) => (m.id === id ? { ...m, enabled } : m))
      );
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(["models", provider.id], ctx.prev);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["providers"] });
      qc.invalidateQueries({ queryKey: ["models", provider.id] });
    },
  });

  const del = useMutation({
    mutationFn: (id: string) => api.deleteModel(provider.id, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["models", provider.id] });
      qc.invalidateQueries({ queryKey: ["providers"] });
    },
  });

  const filtered = useMemo(
    () =>
      models?.filter(
        (m) =>
          !q ||
          m.id.toLowerCase().includes(q.toLowerCase()) ||
          m.name.toLowerCase().includes(q.toLowerCase())
      ) ?? [],
    [models, q]
  );

  const enabledCount = models?.filter((m) => m.enabled).length ?? 0;

  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <h3
          className="text-[13px] font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          Models{" "}
          <span
            className="font-normal"
            style={{ color: "var(--text-muted)" }}
          >
            ({enabledCount}/{models?.length ?? 0} enabled)
          </span>
        </h3>
        <div className="relative w-56">
          <Search
            className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2"
            style={{ color: "var(--text-muted)" }}
          />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search models"
            className="h-8 pl-7 text-[12px]"
            style={{
              background: "var(--bg-input)",
              borderColor: "var(--border-color)",
              color: "var(--text-primary)",
            }}
          />
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : (
        <ul
          className="rounded-md border"
          style={{
            background: "var(--bg-input)",
            borderColor: "var(--border-color)",
          }}
        >
          {filtered.map((m) => (
            <li
              key={m.id}
              className="flex items-center gap-3 border-t px-3 py-2 first:border-t-0"
              style={{ borderColor: "var(--border-color)" }}
            >
              <div className="min-w-0 flex-1">
                <p
                  className="truncate text-[13px] font-medium"
                  style={{ color: "var(--text-primary)" }}
                >
                  {m.name}
                </p>
                <p
                  className="truncate font-mono text-[11px]"
                  style={{ color: "var(--text-muted)" }}
                >
                  {m.id}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                {m.capabilities.map((c) => (
                  <CapBadge key={c} cap={c} context={m.context} />
                ))}
                {m.custom && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0"
                    onClick={() => del.mutate(m.id)}
                  >
                    <Trash2
                      className="h-3.5 w-3.5"
                      style={{ color: "var(--text-muted)" }}
                    />
                  </Button>
                )}
                <Switch
                  checked={m.enabled}
                  onCheckedChange={(enabled) =>
                    toggle.mutate({ id: m.id, enabled })
                  }
                />
              </div>
            </li>
          ))}
          {filtered.length === 0 && (
            <li
              className="py-6 text-center text-[13px]"
              style={{ color: "var(--text-muted)" }}
            >
              No models match &quot;{q}&quot;
            </li>
          )}
        </ul>
      )}
    </section>
  );
}
