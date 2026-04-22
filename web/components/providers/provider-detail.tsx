"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  Eye,
  EyeOff,
  RefreshCw,
  CircleCheck,
  CircleX,
  Loader2,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Provider } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { ModelList } from "./model-list";
import { SetupWizard } from "./setup-wizard";

interface Props {
  provider: Provider;
}

export function ProviderDetail({ provider }: Props) {
  const qc = useQueryClient();
  const [revealKey, setRevealKey] = useState(false);
  const [baseUrl, setBaseUrl] = useState(provider.base_url ?? "");
  const [testResult, setTestResult] = useState<
    { ok: boolean; msg: string } | null
  >(null);
  const [wizardOpen, setWizardOpen] = useState(false);

  const { data: keyPreview } = useQuery({
    queryKey: ["key", provider.api_key_env, revealKey],
    queryFn: () => api.getKey(provider.api_key_env!, revealKey),
    enabled: !!provider.api_key_env,
  });

  const saveBase = useMutation({
    mutationFn: (url: string) =>
      api.setProviderConfig(provider.id, { base_url: url || null }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });

  const test = useMutation({
    mutationFn: () => api.testProvider(provider.id),
    onSuccess: (r) =>
      setTestResult(
        r.ok
          ? { ok: true, msg: `OK · ${r.latency_ms}ms` }
          : { ok: false, msg: r.error ?? "failed" }
      ),
    onError: (e: Error) => setTestResult({ ok: false, msg: e.message }),
  });

  const fetchModels = useMutation({
    mutationFn: () => api.fetchRemoteModels(provider.id),
    onSuccess: (r) => {
      setTestResult({
        ok: true,
        msg: `+${r.added} models (fetched ${r.fetched})`,
      });
      qc.invalidateQueries({ queryKey: ["providers"] });
      qc.invalidateQueries({ queryKey: ["models", provider.id] });
    },
    onError: (e: Error) => setTestResult({ ok: false, msg: e.message }),
  });

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2
            className="text-[18px] font-semibold"
            style={{ color: "var(--text-bright)" }}
          >
            {provider.label}
          </h2>
          <p className="mt-1 text-[13px]" style={{ color: "var(--text-muted)" }}>
            {provider.configured
              ? provider.api_key_env
                ? `Configured via ${provider.api_key_env}`
                : "Subscription required"
              : provider.api_key_env
                ? `Set ${provider.api_key_env} to enable`
                : "Subscription required"}
          </p>
        </div>
        <button
          onClick={() => setWizardOpen(true)}
          className="h-8 shrink-0 rounded-md border px-3 text-[12px]"
          style={{
            background: "transparent",
            borderColor: "var(--border-color)",
            color: "var(--text-primary)",
          }}
        >
          Setup wizard
        </button>
      </header>

      {wizardOpen && (
        <SetupWizard
          providerId={provider.id}
          onClose={() => {
            setWizardOpen(false);
            qc.invalidateQueries({ queryKey: ["providers"] });
          }}
        />
      )}

      {provider.kind === "cli" ? (
        <Card>
          <CardHeader title="CLI" />
          <div
            className="rounded-md p-3 text-[13px]"
            style={{
              background: "var(--bg-input)",
              color: "var(--text-primary)",
            }}
          >
            Runs via{" "}
            <code
              className="rounded px-1 font-mono text-[12px]"
              style={{ background: "var(--bg-tertiary)" }}
            >
              {provider.cli_binary}
            </code>{" "}
            on PATH.
            <div
              className="mt-1 text-[12px]"
              style={{ color: "var(--text-muted)" }}
            >
              {provider.configured
                ? "Binary found. No API key required."
                : "Install the CLI binary to enable."}
            </div>
          </div>
        </Card>
      ) : (
        <>
          <Card>
            <CardHeader title="Connection" />

            {provider.api_key_env && (
              <Row
                label={`API Key (${provider.api_key_env})`}
                control={
                  <div className="flex gap-2">
                    <Input
                      readOnly
                      value={
                        keyPreview
                          ? revealKey
                            ? keyPreview.value ?? ""
                            : keyPreview.masked ?? "(not set)"
                          : "…"
                      }
                      className="h-8 flex-1 font-mono text-[12px]"
                      style={{
                        background: "var(--bg-input)",
                        borderColor: "var(--border-color)",
                        color: "var(--text-primary)",
                      }}
                    />
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-8 w-8 p-0"
                      onClick={() => setRevealKey((v) => !v)}
                      style={{
                        background: "transparent",
                        borderColor: "var(--border-color)",
                        color: "var(--text-primary)",
                      }}
                    >
                      {revealKey ? (
                        <EyeOff className="h-4 w-4" />
                      ) : (
                        <Eye className="h-4 w-4" />
                      )}
                    </Button>
                  </div>
                }
              />
            )}

            <Row
              label="Base URL"
              hint={
                provider.default_base_url
                  ? `default: ${provider.default_base_url}`
                  : undefined
              }
              control={
                <div className="flex gap-2">
                  <Input
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder={provider.default_base_url ?? ""}
                    className="h-8 flex-1 text-[13px]"
                    style={{
                      background: "var(--bg-input)",
                      borderColor: "var(--border-color)",
                      color: "var(--text-primary)",
                    }}
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8"
                    onClick={() => saveBase.mutate(baseUrl)}
                    disabled={saveBase.isPending}
                    style={{
                      background: "transparent",
                      borderColor: "var(--border-color)",
                      color: "var(--text-primary)",
                    }}
                  >
                    Save
                  </Button>
                </div>
              }
            />

            <Row
              label="Use Responses API spec"
              hint="Route to /v1/responses instead of /v1/chat/completions"
              control={
                <Switch
                  checked={provider.use_responses_api}
                  onCheckedChange={(v) =>
                    api
                      .setProviderConfig(provider.id, { use_responses_api: v })
                      .then(() =>
                        qc.invalidateQueries({ queryKey: ["providers"] })
                      )
                  }
                />
              }
            />

            <div className="flex items-center justify-between pt-3">
              <div
                className="flex-1 truncate text-[12px]"
                style={{ color: "var(--text-muted)" }}
              >
                {testResult && (
                  <span
                    style={{
                      color: testResult.ok
                        ? "var(--accent-green)"
                        : "var(--accent-red)",
                    }}
                  >
                    {testResult.ok ? "✓ " : "✗ "}
                    {testResult.msg}
                  </span>
                )}
              </div>
              <div className="flex gap-2">
                {provider.supports_fetch && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8"
                    onClick={() => fetchModels.mutate()}
                    disabled={fetchModels.isPending}
                    style={{
                      background: "transparent",
                      borderColor: "var(--border-color)",
                      color: "var(--text-primary)",
                    }}
                  >
                    {fetchModels.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <RefreshCw className="h-3.5 w-3.5" />
                    )}
                    <span className="ml-1">Fetch models</span>
                  </Button>
                )}
                <Button
                  size="sm"
                  className="h-8"
                  onClick={() => test.mutate()}
                  disabled={test.isPending}
                  style={{
                    background: "var(--accent-blue)",
                    color: "#fff",
                  }}
                >
                  {test.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : testResult?.ok ? (
                    <CircleCheck className="h-3.5 w-3.5" />
                  ) : testResult && !testResult.ok ? (
                    <CircleX className="h-3.5 w-3.5" />
                  ) : null}
                  <span className="ml-1">Check</span>
                </Button>
              </div>
            </div>
          </Card>

          <Card>
            <ModelList provider={provider} />
          </Card>
        </>
      )}
    </div>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="rounded-[10px] border px-5 py-4"
      style={{
        background: "var(--bg-secondary)",
        borderColor: "var(--border-color)",
      }}
    >
      {children}
    </div>
  );
}

function CardHeader({ title }: { title: string }) {
  return (
    <h3
      className="mb-3 text-[13px] font-semibold"
      style={{ color: "var(--text-primary)" }}
    >
      {title}
    </h3>
  );
}

function Row({
  label,
  hint,
  control,
}: {
  label: string;
  hint?: string;
  control: React.ReactNode;
}) {
  return (
    <div
      className="flex items-center gap-4 border-t py-3 first:border-t-0 first:pt-0"
      style={{ borderColor: "var(--border-color)" }}
    >
      <div className="min-w-0 flex-1">
        <div className="text-[13px]" style={{ color: "var(--text-primary)" }}>
          {label}
        </div>
        {hint && (
          <div
            className="mt-0.5 truncate text-[11px]"
            style={{ color: "var(--text-muted)" }}
          >
            {hint}
          </div>
        )}
      </div>
      <div className="w-[320px] max-w-[60%] shrink-0">{control}</div>
    </div>
  );
}
