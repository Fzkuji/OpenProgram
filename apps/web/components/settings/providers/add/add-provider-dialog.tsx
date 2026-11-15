"use client";

import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { ArrowLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useTranslation } from "@/lib/i18n";
import { api } from "@/lib/net/api";
import { cachedFetch, invalidate } from "@/lib/prefs/settings-cache";
import type { DetectedEnvKey, DetectedPayload, KeyCheck, SuggestedModel } from "@/lib/settings/provider-status.generated";

import { AddCustomProvider } from "../add-custom-provider";
import { BaseUrl } from "../auth/base-url";
import { ProviderLogin } from "../auth/provider-login";
import { SetupHint } from "../auth/setup-hint";
import { ProviderIcon } from "../icons/provider-icon";
import { STATUS_URL } from "../status/use-provider-status";
import { fmtContext, fmtPrice } from "../status/model";
import { HealthChip } from "../status/parts";
import styles from "../status/status.module.css";
import { refreshAgentChip, type Model, type Provider } from "../types";
import { CATALOGUE_URL, DETECTED_URL, envKeysFor, hostOf, suggestFromModels, type Choice, type Stored } from "./data";
import { CheckLine, KeyEntry, USABLE } from "./key-entry";
import { EnvKeyRow, PickProvider } from "./pick";

/** Where the dialog opens: the picker, step 2 for one provider, or an
 *  environment key's Use (stored, then straight to step 3). */
export type AddStart =
  | { step: "pick" }
  | { step: "custom" }
  | { step: "connect"; choice: Choice }
  | { step: "env"; row: DetectedEnvKey };

type Suggestion = { top: SuggestedModel[]; count: number; enabled: string[]; providerEnabled: boolean };

type Step =
  | { step: "pick" }
  | { step: "custom" }
  | { step: "connect"; choice: Choice; family: Choice[]; check: KeyCheck | null; envVar: string | null; busy: boolean }
  | { step: "model"; choice: Choice; suggestion: Suggestion };

const JSON_HEADERS = { "Content-Type": "application/json" };

function suggestionOf(check: KeyCheck): Suggestion {
  return { top: check.models, count: check.model_count ?? check.models.length, enabled: check.enabled_models, providerEnabled: check.provider_enabled };
}

async function readModels(id: string): Promise<Model[]> {
  const r = await fetch(`/api/providers/${encodeURIComponent(id)}/models`);
  if (!r.ok) return [];
  const d = await r.json().catch(() => ({}));
  return (d.models as Model[]) ?? [];
}

/** Add provider (design §4): pick → key or sign-in, checked automatically,
 *  with a one-line result → default model → Start chatting. */
export function AddProviderDialog({
  open, start, detected, detectedFailed = false, onRetryDetected, catalogue, onOpenChange, onConnected, onOpenProvider,
}: {
  open: boolean;
  start: AddStart;
  detected: DetectedPayload | null;
  detectedFailed?: boolean;
  onRetryDetected?: () => void;
  catalogue: Provider[];
  onOpenChange: (open: boolean) => void;
  /** Something was stored: the page re-reads its lists. */
  onConnected: () => void;
  /** Open a provider's detail page (custom provider created, cloud setup). */
  onOpenProvider: (id: string) => void;
}) {
  const { text } = useTranslation();
  const queryClient = useQueryClient();
  const router = useRouter();
  const [step, setStep] = useState<Step>({ step: "pick" });
  const [model, setModel] = useState("");
  const [usingEnv, setUsingEnv] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const entryOf = useCallback((id: string) => catalogue.find((p) => p.id === id), [catalogue]);

  const changed = useCallback(() => {
    invalidate(STATUS_URL); invalidate(DETECTED_URL); invalidate(CATALOGUE_URL);
    queryClient.invalidateQueries({ queryKey: ["models-enabled"] });
    refreshAgentChip();
    onConnected();
  }, [onConnected, queryClient]);

  const toModelStep = useCallback((choice: Choice, suggestion: Suggestion) => {
    setModel(suggestion.top[0]?.id ?? "");
    setStep({ step: "model", choice, suggestion });
  }, []);

  /** Step 2 for a choice; `family` is the vendor's ways to connect (the tile and its variants). */
  const connectChoice = (choice: Choice, family?: Choice[]): Step => ({
    step: "connect", choice, check: null, envVar: null, busy: false,
    family: family ?? [choice, ...choice.variants.map((v) => ({ ...v, variants: [] }))],
  });

  /** Use on an environment key: the Worker reads, checks and stores it. From
   *  step 2 it is stored for the provider on screen (`target`), whose ways
   *  to connect stay as they were; from the pick list, for the row's provider. */
  const storeEnvKey = useCallback(async (row: DetectedEnvKey, target?: { choice: Choice; family: Choice[] }) => {
    const base: Choice = target?.choice ?? { id: row.provider, label: row.label, auth: "api_key", console_url: null, env_var: row.env_var, variants: [] };
    const popular = detected?.popular.find((c) => c.id === base.id);
    const choice: Choice = { ...base, console_url: base.console_url ?? popular?.console_url ?? null };
    const family = target?.family.map((c) => (c.id === choice.id ? choice : c)) ?? [choice];
    setUsingEnv(row.env_var);
    setStep({ step: "connect", choice, family, check: null, envVar: row.env_var, busy: true });
    try {
      const r = await fetch(`/api/providers/${encodeURIComponent(choice.id)}/accounts/env`, {
        method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ env_var: row.env_var }),
      });
      const d = await r.json().catch(() => ({}));
      const check = (d.check ?? null) as KeyCheck | null;
      if (r.ok && d.ok) {
        changed();
        if (check && USABLE.has(check.result)) { toModelStep(choice, suggestionOf(check)); return; }
      }
      setStep({ step: "connect", choice, family, check: check ?? { result: "rejected", http_status: r.status, detail: d.error ?? null, fix_page: null, fix_url: null, balance: null, model_count: null, models: [], enabled_models: [], provider_enabled: false, auto_enabled: false }, envVar: row.env_var, busy: false });
    } catch {
      setStep({ step: "connect", choice, family, check: null, envVar: null, busy: false });
    } finally {
      setUsingEnv(null);
    }
  }, [changed, detected, toModelStep]);

  // Every open starts where it was asked to.
  useEffect(() => {
    if (!open) return;
    setShowAdvanced(false);
    setModel("");
    if (start.step === "pick" || start.step === "custom") setStep({ step: start.step });
    else if (start.step === "connect") setStep(connectChoice(start.choice));
    else void storeEnvKey(start.row);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, start]);

  const fromModels = useCallback(async (choice: Choice) => {
    const models = await readModels(choice.id);
    invalidate(CATALOGUE_URL);
    const list = await cachedFetch<{ providers?: Provider[] }>(CATALOGUE_URL).catch(() => ({ providers: [] as Provider[] }));
    const enabled = !!list.providers?.find((p) => p.id === choice.id)?.enabled;
    const s = suggestFromModels(models);
    toModelStep(choice, { ...s, providerEnabled: enabled });
  }, [toModelStep]);

  async function connectLocal(choice: Choice) {
    if (step.step !== "connect") return;
    setStep({ ...step, busy: true });
    await fetch(`/api/providers/${encodeURIComponent(choice.id)}/toggle`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ enabled: true }) }).catch(() => null);
    await fetch(`/api/providers/${encodeURIComponent(choice.id)}/fetch-models`, { method: "POST", headers: JSON_HEADERS, body: "{}" }).catch(() => null);
    changed();
    await fromModels(choice);
  }

  async function startChatting() {
    if (step.step !== "model" || !model) return;
    const id = step.choice.id;
    setStarting(true);
    try {
      if (!step.suggestion.providerEnabled) {
        await fetch(`/api/providers/${encodeURIComponent(id)}/toggle`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ enabled: true }) });
      }
      if (!step.suggestion.enabled.includes(model)) {
        await fetch(`/api/providers/${encodeURIComponent(id)}/models/${encodeURIComponent(model)}/toggle`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ enabled: true }) });
      }
      // No conversation named: the pick becomes the default a new chat starts with.
      await api.switchModel(id, model);
      changed();
      onOpenChange(false);
      router.push("/chat");
    } finally {
      setStarting(false);
    }
  }

  const choice = step.step === "connect" || step.step === "model" ? step.choice : null;
  const entry = choice ? entryOf(choice.id) : undefined;
  const variants: Choice[] = step.step === "connect" ? step.family : [];
  const consoleUrl = choice?.console_url ?? null;

  let head: React.ReactNode;
  let body: React.ReactNode;
  let foot: React.ReactNode = null;

  if (step.step === "pick") {
    head = <DialogTitle className={styles.dialogHead}>{text("Add provider", "添加服务商")}</DialogTitle>;
    body = (
      <PickProvider
        detected={detected}
        detectedFailed={detectedFailed}
        onRetry={onRetryDetected}
        catalogue={catalogue}
        autoFocus
        usingEnv={usingEnv}
        onPick={(c) => setStep(connectChoice(c))}
        onUseEnv={(row) => { void storeEnvKey(row); }}
        onCustom={() => setStep({ step: "custom" })}
      />
    );
  } else if (step.step === "custom") {
    head = (
      <DialogTitle className={styles.dialogHead}>
        <Button variant="ghost" size="icon-sm" aria-label={text("Back", "返回")} onClick={() => setStep({ step: "pick" })}><ArrowLeft /></Button>
        {text("Custom provider", "自定义服务商")}
      </DialogTitle>
    );
    body = (
      <AddCustomProvider defaultOpen onCancel={() => setStep({ step: "pick" })}
        onCreated={(id) => { changed(); onOpenChange(false); onOpenProvider(id); }} />
    );
  } else if (step.step === "connect" && choice) {
    head = (
      <DialogTitle className={styles.dialogHead}>
        <Button variant="ghost" size="icon-sm" aria-label={text("Back", "返回")} onClick={() => setStep({ step: "pick" })}><ArrowLeft /></Button>
        <ProviderIcon id={choice.id} size={28} />
        <span>{choice.label}</span>
      </DialogTitle>
    );
    const usable = !!step.check && USABLE.has(step.check.result);
    // A key for this provider already in the Worker's environment comes
    // first, one Use away; pasting a key is the alternative.
    const envKeys = choice.auth === "api_key" ? envKeysFor(detected, choice.id) : [];
    const envChecked = !!step.envVar && !!step.check;
    body = (
      <>
        {variants.length > 1 ? (
          <Tabs value={choice.id} onValueChange={(id) => { const v = variants.find((c) => c.id === id); if (v) setStep(connectChoice(v, variants)); }}>
            <TabsList aria-label={text("How to connect", "接入方式")}>
              {variants.map((v) => (
                <TabsTrigger key={v.id} value={v.id} className="px-3">
                  {v.label} <span className={styles.muted}>· {v.auth === "sign_in" ? text("Sign in", "登录") : v.auth === "local" ? text("Local", "本地") : text("API key", "API key")}</span>
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        ) : null}
        {step.busy && step.envVar ? (
          <div className={`${styles.line} ${styles.wait}`} role="status"><Spinner /><span>{text(`Checking the key in ${step.envVar}…`, `正在检查 ${step.envVar} 里的密钥…`)}</span></div>
        ) : step.envVar && step.check && choice.auth !== "api_key" ? (
          <CheckLine check={step.check} consoleUrl={consoleUrl} host={hostOf(entry)} />
        ) : choice.auth === "api_key" ? (
          // One fixed slot per part, so the paste field keeps its result when
          // a key pasted after a rejected environment key is stored.
          <>
            {envChecked ? (
              <CheckLine check={step.check!} consoleUrl={consoleUrl} host={hostOf(entry)} />
            ) : envKeys.length > 0 ? (
              <>
                <div className={styles.group}>{text("In your environment", "环境变量里已有")}</div>
                {envKeys.map((row) => (
                  <EnvKeyRow key={row.env_var} row={row} label={choice.label} icon={choice.id} usingEnv={usingEnv}
                    onUse={(r) => { void storeEnvKey(r, { choice, family: variants }); }} />
                ))}
              </>
            ) : null}
            {/* Pasting is the alternative to an environment key, and the way
                on when the one used was rejected. */}
            {(envChecked ? !usable : envKeys.length > 0) ? <div className={styles.group}>{text("Or paste a key", "或粘贴密钥")}</div> : null}
            {envChecked && usable ? null : (
              <KeyEntry
                key={choice.id}
                providerId={choice.id}
                consoleUrl={consoleUrl}
                host={hostOf(entry)}
                autoFocus={!envChecked && envKeys.length === 0}
                onStored={(stored: Stored) => {
                  changed();
                  setStep({ step: "connect", choice, family: variants, check: stored.check, envVar: null, busy: false });
                }}
              />
            )}
            {!envChecked && entry && (entry.api_key_env || entry.local) ? (
              <>
                <button type="button" className={styles.fold} aria-expanded={showAdvanced} onClick={() => setShowAdvanced((v) => !v)}>
                  <ChevronRight aria-hidden="true" />{text("Advanced", "高级")}
                </button>
                {showAdvanced ? <BaseUrl provider={entry} onChanged={() => changed()} /> : null}
              </>
            ) : null}
          </>
        ) : choice.auth === "sign_in" ? (
          entry && choice.id !== "anthropic" ? (
            <ProviderLogin provider={entry} bare onChanged={() => { changed(); void fromModels(choice); }} />
          ) : (
            <div className={`${styles.xs} ${styles.muted}`}>{text("Sign-in is not available here", "这里不能登录")}</div>
          )
        ) : choice.auth === "local" ? (
          <>
            {(() => {
              const local = detected?.local.find((l) => l.id === choice.id);
              return local && !local.running ? (
                <>
                  <HealthChip health={{ state: "not_running", until: null, detail: null }} />
                  {entry?.setup_hint ? <SetupHint hint={entry.setup_hint} configured={!!entry.configured} /> : null}
                </>
              ) : local ? <div className={`${styles.xs} ${styles.muted} ${styles.mono}`}>{local.base_url}</div> : null;
            })()}
          </>
        ) : (
          <Button variant="elevated" onClick={() => { onOpenChange(false); onOpenProvider(choice.id); }}>{text("Set up on its page", "到它的页面设置")}</Button>
        )}
      </>
    );
    if (choice.auth === "local") {
      foot = <Button size="sm" onClick={() => void connectLocal(choice)} disabled={step.busy}>{step.busy ? <Spinner /> : null}{text("Connect", "接入")}</Button>;
    } else if (choice.auth === "api_key" || step.envVar) {
      foot = (
        <Button size="sm" disabled={!usable} onClick={() => step.check && toModelStep(choice, suggestionOf(step.check))}>
          {text("Continue", "继续")}
        </Button>
      );
    }
  } else if (step.step === "model") {
    head = (
      <DialogTitle className={styles.dialogHead}>
        <ProviderIcon id={step.choice.id} size={28} />
        <span>{step.choice.label}</span>
        <HealthChip health={{ state: "ready", until: null, detail: null }} label={text("Connected", "已接入")} />
      </DialogTitle>
    );
    const { top, count, enabled } = step.suggestion;
    const more = Math.max(0, count - top.length);
    body = top.length ? (
      <>
        <RadioGroup value={model} onValueChange={setModel} aria-label={text("Default model", "默认模型")} className="gap-1">
          {top.map((m) => (
            <label key={m.id} className={styles.modelChoice} data-model={m.id}>
              <RadioGroupItem value={m.id} />
              <span className={styles.mono}>{m.id}</span>
              <span className="flex-1" />
              <span className={`${styles.modelMeta} ${styles.num}`}>
                {[fmtContext(m.context_window), m.input_cost != null || m.output_cost != null ? `$${fmtPrice(m.input_cost)} / $${fmtPrice(m.output_cost)}` : null].filter(Boolean).join(" · ")}
              </span>
            </label>
          ))}
        </RadioGroup>
        {more > 0 ? (
          <div className={`${styles.xs} ${styles.muted}`} style={{ padding: "0 10px" }}>
            {text(`+${more} more · ${enabled.length} enabled`, `另有 ${more} 个 · 已启用 ${enabled.length} 个`)}
          </div>
        ) : null}
      </>
    ) : (
      <div className={`${styles.xs} ${styles.muted}`}>{text("No models listed yet", "还没有列出模型")}</div>
    );
    foot = top.length ? (
      <Button size="sm" onClick={() => void startChatting()} disabled={!model || starting}>
        {starting ? <Spinner /> : null}{text("Start chatting", "开始对话")}
      </Button>
    ) : (
      <Button size="sm" onClick={() => { onOpenChange(false); onOpenProvider(step.choice.id); }}>{text("Open provider", "打开服务商")}</Button>
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={styles.dialog} data-step={step.step}>
        {head}
        <DialogDescription className="sr-only">{text("Connect a model provider", "接入模型服务商")}</DialogDescription>
        <div className="flex flex-col gap-[12px] px-[18px] pb-[16px] max-h-[min(70vh,640px)] overflow-y-auto min-w-0">{body}</div>
        {foot ? <div className={styles.dialogFoot}><span className="flex-1" />{foot}</div> : null}
      </DialogContent>
    </Dialog>
  );
}
