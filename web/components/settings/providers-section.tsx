"use client";

/**
 * LLM Providers settings — port of /js/shared/settings-providers.js (521 lines).
 *
 * Two-pane layout: provider list (search + grouped by enabled/disabled)
 * on the left; detail pane on the right with enable toggle, API key
 * input (mask/reveal/save), base URL override, connectivity check, and
 * model list (toggle / search / fetch remote / bulk enable / disable).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Eye, Wrench, Brain, Video } from "lucide-react";
import styles from "./settings-page.module.css";
import { ProviderIcon } from "./provider-icon";

interface Provider {
  id: string;
  label: string;
  enabled: boolean;
  configured?: boolean;
  kind?: "api" | "cli";
  api_key_env?: string;
  default_base_url?: string;
  base_url?: string;
  supports_fetch?: boolean;
  cli_binary?: string;
  /**
   * Provider-specific setup instructions surfaced in the detail
   * panel. Backticked spans render as inline <code>; lines starting
   * with `$ ` render as a command row. Used by claude-max-proxy /
   * any other "local daemon" provider whose setup isn't an API key.
   */
  setup_hint?: string;
}

interface Model {
  id: string;
  name?: string;
  enabled: boolean;
  vision?: boolean;
  video?: boolean;
  tools?: boolean;
  reasoning?: boolean;
  context_window?: number;
}

function formatCtx(n?: number) {
  if (!n) return "";
  if (n >= 1_000_000) return Math.round(n / 1_000_000) + "M";
  if (n >= 1000) return Math.round(n / 1000) + "K";
  return String(n);
}

export function ProvidersSection() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const sidebarRef = useRef<HTMLDivElement>(null);

  // Forward wheel events to the page scroll once the sidebar's own
  // scroll is at top/bottom. Browsers' default scroll-chaining only
  // kicks in on the NEXT wheel event after the boundary is hit, so a
  // user trying to scroll past the end of a long provider list sees
  // their small wheel ticks get consumed without page movement until
  // they "force" a larger gesture. We rAF-accumulate deltas so the
  // forwarded scroll feels as smooth as the browser's own.
  useEffect(() => {
    const sb = sidebarRef.current;
    if (!sb) return;
    let pendingDelta = 0;
    let rafId = 0;
    let scrollTarget: HTMLElement | null = null;
    function flush() {
      rafId = 0;
      if (!scrollTarget || pendingDelta === 0) return;
      scrollTarget.scrollTop += pendingDelta;
      pendingDelta = 0;
    }
    function onWheel(e: WheelEvent) {
      const el = sidebarRef.current;
      if (!el) return;
      const atTop = el.scrollTop === 0;
      const atBottom =
        Math.ceil(el.scrollTop + el.clientHeight) >= el.scrollHeight;
      if ((e.deltaY < 0 && atTop) || (e.deltaY > 0 && atBottom)) {
        // Find the closest scrollable ancestor once and cache it.
        if (!scrollTarget) {
          let p: HTMLElement | null = el.parentElement;
          while (p) {
            const cs = getComputedStyle(p);
            if (
              (cs.overflowY === "auto" || cs.overflowY === "scroll") &&
              p.scrollHeight > p.clientHeight
            ) {
              scrollTarget = p;
              break;
            }
            p = p.parentElement;
          }
        }
        if (!scrollTarget) return;
        e.preventDefault();
        pendingDelta += e.deltaY;
        if (!rafId) rafId = requestAnimationFrame(flush);
      }
    }
    sb.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      sb.removeEventListener("wheel", onWheel);
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, []);

  const reload = useCallback(async (preserveSelection?: boolean) => {
    let list: Provider[] = [];
    try {
      const r = await fetch("/api/providers/list");
      const d = await r.json();
      list = d.providers || [];
    } catch {
      /* empty */
    }
    setProviders(list);
    if (!preserveSelection && list.length > 0) {
      const first = list.find((p) => p.enabled) || list[0];
      setSelectedId(first.id);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const enabled = providers.filter((p) => p.enabled);
  const disabled = providers.filter((p) => !p.enabled);
  const selected = providers.find((p) => p.id === selectedId) || null;

  function matches(p: Provider) {
    if (!search) return true;
    const q = search.toLowerCase();
    return p.label.toLowerCase().includes(q) || p.id.toLowerCase().includes(q);
  }

  async function toggleProvider(id: string, en: boolean) {
    try {
      await fetch(`/api/providers/${encodeURIComponent(id)}/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: en }),
      });
    } catch {
      /* ignore */
    }
    reload(true);
  }

  return (
    <div className={styles.section}>
      <div className={styles.providersLayout}>
        <div className={styles.providersSidebar} ref={sidebarRef}>
          <h2 className={styles.sectionTitle}>AI Providers</h2>
          <div className={styles.providersSearch}>
            <input
              type="search"
              placeholder="Search providers…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          {enabled.filter(matches).length > 0 && (
            <>
              <div className={styles.providersGroupLabel}>Enabled</div>
              {enabled.filter(matches).map((p) => (
                <ProviderItem
                  key={p.id}
                  p={p}
                  active={selectedId === p.id}
                  onSelect={() => setSelectedId(p.id)}
                />
              ))}
            </>
          )}
          {disabled.filter(matches).length > 0 && (
            <>
              <div className={styles.providersGroupLabel}>Not enabled</div>
              {disabled.filter(matches).map((p) => (
                <ProviderItem
                  key={p.id}
                  p={p}
                  active={selectedId === p.id}
                  onSelect={() => setSelectedId(p.id)}
                />
              ))}
            </>
          )}
        </div>
        <div className={styles.detail}>
          {!selected ? (
            <div className={styles.detailEmpty}>Select a provider on the left</div>
          ) : (
            <Detail
              key={selected.id}
              provider={selected}
              onToggle={(en) => toggleProvider(selected.id, en)}
              onChanged={() => reload(true)}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function ProviderItem({
  p,
  active,
  onSelect,
}: {
  p: Provider;
  active: boolean;
  onSelect: () => void;
}) {
  const dot = p.enabled ? "on" : p.configured ? "off" : "unconfigured";
  return (
    <div
      className={styles.providerItem + (active ? " " + styles.active : "")}
      onClick={onSelect}
    >
      <ProviderIcon id={p.id} size={24} />
      <span className={styles.providerLabel}>{p.label}</span>
      <span
        className={
          styles.providerDot +
          " " +
          (dot === "on" ? styles.on : dot === "off" ? styles.off : styles.unconfigured)
        }
        title={
          p.enabled ? "Enabled" : p.configured ? "Not enabled" : "Not configured"
        }
      />
    </div>
  );
}

function Detail({
  provider,
  onToggle,
  onChanged,
}: {
  provider: Provider;
  onToggle: (enabled: boolean) => void;
  onChanged: () => void;
}) {
  const subtitle =
    provider.kind === "cli"
      ? `CLI runtime — binary: ${provider.cli_binary || "?"}`
      : provider.api_key_env
        ? `API key env: ${provider.api_key_env}`
        : "Subscription required";

  const [models, setModels] = useState<Model[]>([]);
  const [modelSearch, setModelSearch] = useState("");

  const reloadModels = useCallback(async () => {
    if (provider.kind === "cli") {
      setModels([]);
      return;
    }
    try {
      const r = await fetch(
        `/api/providers/${encodeURIComponent(provider.id)}/models`,
      );
      const d = await r.json();
      setModels(d.models || []);
    } catch {
      setModels([]);
    }
  }, [provider.id, provider.kind]);

  useEffect(() => {
    reloadModels();
  }, [reloadModels]);

  return (
    <>
      <div className={styles.detailHeader}>
        <div className={styles.detailIcon}>
          <ProviderIcon id={provider.id} size={40} />
        </div>
        <div className={styles.detailTitleWrap}>
          <div className={styles.detailTitle}>{provider.label}</div>
          <div className={styles.detailSubtitle}>{subtitle}</div>
        </div>
        <label className={styles.toggleSwitch} title="Enable this provider">
          <input
            type="checkbox"
            checked={provider.enabled}
            onChange={(e) => onToggle(e.target.checked)}
          />
          <span className={styles.slider} />
        </label>
      </div>

      {provider.setup_hint && (
        <SetupHint hint={provider.setup_hint} configured={!!provider.configured} />
      )}

      {provider.api_key_env && (
        <ApiKey envVar={provider.api_key_env} configured={!!provider.configured} onChanged={onChanged} />
      )}
      {provider.api_key_env && (
        <BaseUrl provider={provider} onChanged={onChanged} />
      )}
      {provider.api_key_env && <Connectivity providerId={provider.id} />}

      {provider.kind === "cli" ? (
        <CliInfo provider={provider} />
      ) : models.length > 0 ? (
        <ModelList provider={provider} models={models} search={modelSearch} onSearch={setModelSearch} onReload={reloadModels} />
      ) : (
        <div className={styles.detailSection}>
          <p className={styles.modelCountSummary}>
            No models in the registry for this provider.
          </p>
        </div>
      )}
    </>
  );
}

function SetupHint({ hint, configured }: { hint: string; configured: boolean }) {
  // Tiny markdown subset: backticked spans → <code>, lines starting
  // with "$ " → command rows. Avoids pulling a markdown lib for what
  // is essentially a small static help blurb per provider.
  const lines = hint.split("\n");
  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>Setup</span>
        <span className={styles.modelCountSummary}>
          {configured ? "Detected" : "Not running"}
        </span>
      </div>
      <div style={{ color: "var(--text-muted)", fontSize: 13, lineHeight: 1.55 }}>
        {lines.map((line, i) => {
          const isCmd = line.startsWith("$ ");
          if (isCmd) {
            return (
              <pre
                key={i}
                style={{
                  margin: "4px 0",
                  padding: "6px 10px",
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  overflow: "auto",
                }}
              >
                {line.slice(2)}
              </pre>
            );
          }
          const segments = line.split(/(`[^`]+`)/g).map((seg, j) => {
            if (seg.startsWith("`") && seg.endsWith("`")) {
              return (
                <code
                  key={j}
                  style={{
                    background: "var(--bg-tertiary)",
                    padding: "0 4px",
                    borderRadius: 3,
                    fontSize: 12,
                  }}
                >
                  {seg.slice(1, -1)}
                </code>
              );
            }
            return <span key={j}>{seg}</span>;
          });
          return <div key={i}>{segments.length && line ? segments : <br />}</div>;
        })}
      </div>
    </div>
  );
}

function CliInfo({ provider }: { provider: Provider }) {
  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>CLI Binary</span>
        <span className={styles.modelCountSummary}>
          {provider.configured ? "Found in PATH" : "Not found"}
        </span>
      </div>
      <p style={{ color: "var(--text-muted)", fontSize: 13 }}>
        This provider wraps the <code>{provider.cli_binary}</code> CLI. Install it
        and run its own login command; enable the toggle above to use it here.
      </p>
    </div>
  );
}

export function ApiKey({
  envVar,
  configured,
  onChanged,
}: {
  envVar: string;
  configured: boolean;
  onChanged: () => void;
}) {
  const [value, setValue] = useState("");
  const [state, setState] = useState<"empty" | "masked" | "editing" | "revealed">("empty");
  const [showText, setShowText] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const loadPreview = useCallback(async () => {
    try {
      const r = await fetch(`/api/config/key/${encodeURIComponent(envVar)}`);
      const d = await r.json();
      if (d.has_value) {
        setValue(d.masked || "");
        setState("masked");
        setShowText(false);
      } else {
        setValue("");
        setState("empty");
      }
    } catch {
      /* ignore */
    }
  }, [envVar]);

  useEffect(() => {
    loadPreview();
  }, [loadPreview]);

  async function toggleVisibility() {
    if (state === "empty" || state === "editing") {
      setShowText((v) => !v);
      return;
    }
    if (state === "masked") {
      try {
        const r = await fetch(`/api/config/key/${encodeURIComponent(envVar)}?reveal=1`);
        const d = await r.json();
        if (d.has_value) {
          setValue(d.value || "");
          setShowText(true);
          setState("revealed");
        }
      } catch { /* ignore */ }
    } else {
      try {
        const r = await fetch(`/api/config/key/${encodeURIComponent(envVar)}`);
        const d = await r.json();
        if (d.has_value) {
          setValue(d.masked || "");
          setShowText(false);
          setState("masked");
        }
      } catch { /* ignore */ }
    }
  }

  function onInput(v: string) {
    if (state === "masked" || state === "revealed") {
      setValue("");
      setShowText(false);
      setState("editing");
      return;
    }
    setValue(v);
  }

  async function save() {
    const v = value.trim();
    if (!v || v.indexOf("...") >= 0) return;
    try {
      const r = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_keys: { [envVar]: v } }),
      });
      const d = await r.json();
      if (d.saved) {
        setValue("");
        if (inputRef.current) inputRef.current.placeholder = `${envVar} (saved)`;
        onChanged();
        loadPreview();
      }
    } catch { /* ignore */ }
  }

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>API Key</span>
        <span className={styles.modelCountSummary}>
          {configured ? "Configured" : "Not set"}
        </span>
      </div>
      <div className={styles.detailRow}>
        <input
          ref={inputRef}
          className={styles.input}
          type={showText ? "text" : "password"}
          placeholder={envVar}
          value={value}
          onChange={(e) => onInput(e.target.value)}
        />
        <button
          className={styles.iconBtn}
          title="Show/hide"
          onClick={toggleVisibility}
        >
          {showText ? "🙈" : "👁"}
        </button>
        <button className={styles.btn} onClick={save}>
          Save
        </button>
      </div>
    </div>
  );
}

function BaseUrl({
  provider,
  onChanged,
}: {
  provider: Provider;
  onChanged: () => void;
}) {
  const [value, setValue] = useState(provider.base_url || "");
  const baseDefault = provider.default_base_url
    ? `default: ${provider.default_base_url}`
    : "";

  async function save() {
    try {
      await fetch(`/api/providers/${encodeURIComponent(provider.id)}/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_url: value.trim() }),
      });
      onChanged();
    } catch { /* ignore */ }
  }

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>API Base URL</span>
        <span className={styles.modelCountSummary}>{baseDefault}</span>
      </div>
      <div className={styles.detailRow}>
        <input
          className={styles.input}
          type="text"
          placeholder={provider.default_base_url || "https://..."}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <button className={styles.btn} onClick={save}>
          Save
        </button>
      </div>
    </div>
  );
}

function Connectivity({ providerId }: { providerId: string }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ kind: "ok" | "err"; text: string; title?: string } | null>(null);

  async function test() {
    setBusy(true);
    setResult({ kind: "ok", text: "…" });
    try {
      const r = await fetch(`/api/providers/${encodeURIComponent(providerId)}/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const d = await r.json();
      if (d.ok) {
        setResult({
          kind: "ok",
          text: `✓ ${d.latency_ms || 0} ms`,
          title: d.model ? `Tested with ${d.model}` : undefined,
        });
      } else {
        setResult({ kind: "err", text: "✗ failed", title: d.error });
      }
    } catch (e) {
      setResult({ kind: "err", text: "✗", title: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>Connectivity check</span>
      </div>
      <div className={styles.detailRow}>
        <span className={styles.modelCountSummary} style={{ flex: 1 }}>
          Validates API key + base URL with a tiny PING.
        </span>
        {result && (
          <span className={styles.testResult + " " + (result.kind === "ok" ? styles.ok : styles.err)} title={result.title}>
            {result.text}
          </span>
        )}
        <button className={styles.btn} onClick={test} disabled={busy}>
          Check
        </button>
      </div>
    </div>
  );
}

function ModelList({
  provider,
  models,
  search,
  onSearch,
  onReload,
}: {
  provider: Provider;
  models: Model[];
  search: string;
  onSearch: (s: string) => void;
  onReload: () => void;
}) {
  const enabledCount = models.filter((m) => m.enabled).length;
  const filtered = !search
    ? models
    : models.filter((m) => {
        const q = search.toLowerCase();
        return (
          (m.name || "").toLowerCase().includes(q) ||
          (m.id || "").toLowerCase().includes(q)
        );
      });

  async function toggle(modelId: string, enabled: boolean) {
    try {
      await fetch(
        `/api/providers/${encodeURIComponent(provider.id)}/models/${encodeURIComponent(modelId)}/toggle`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled }),
        },
      );
    } catch { /* ignore */ }
    onReload();
  }

  async function bulkToggle(enabled: boolean) {
    const targets = models.filter((m) => m.enabled !== enabled);
    await Promise.all(
      targets.map((m) =>
        fetch(
          `/api/providers/${encodeURIComponent(provider.id)}/models/${encodeURIComponent(m.id)}/toggle`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled }),
          },
        ),
      ),
    );
    onReload();
  }

  const [fetchStatus, setFetchStatus] = useState<string | null>(null);
  async function fetchRemote() {
    setFetchStatus("Fetching…");
    try {
      const r = await fetch(
        `/api/providers/${encodeURIComponent(provider.id)}/fetch-models`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        },
      );
      const d = await r.json();
      if (d.error) {
        setFetchStatus("Failed: " + d.error);
        // Auto-clear failure message after 6s so the row resets.
        setTimeout(() => setFetchStatus(null), 6_000);
        return;
      }
      // Brief summary; added > 0 → new rows merged; added == 0 →
      // registry already had everything the provider returned.
      const summary = d.added > 0
        ? `Fetched ${d.fetched}, added ${d.added} new`
        : `Fetched ${d.fetched} — already up to date`;
      setFetchStatus(summary);
      onReload();
      setTimeout(() => setFetchStatus(null), 4_000);
    } catch (e) {
      setFetchStatus("Failed: " + (e as Error).message);
      setTimeout(() => setFetchStatus(null), 6_000);
    }
  }

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>
          Models{" "}
          <span className={styles.modelCountSummary}>
            {enabledCount} / {models.length} available
          </span>
        </span>
        <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {fetchStatus && (
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {fetchStatus}
            </span>
          )}
          {provider.supports_fetch && (
            <button className={styles.miniAction} onClick={fetchRemote}>
              Fetch models
            </button>
          )}
          <button className={styles.miniAction} onClick={() => bulkToggle(true)}>
            Enable all
          </button>
          <button className={styles.miniAction} onClick={() => bulkToggle(false)}>
            Disable all
          </button>
        </span>
      </div>
      <div className={styles.modelSearch}>
        <input
          type="search"
          placeholder="Search models…"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
        />
      </div>
      <div className={styles.modelList}>
        {filtered.map((m) => (
          <ModelRow
            key={m.id}
            providerId={provider.id}
            model={m}
            onToggle={(en) => toggle(m.id, en)}
          />
        ))}
      </div>
    </div>
  );
}

function ModelRow({
  providerId,
  model,
  onToggle,
}: {
  providerId: string;
  model: Model;
  onToggle: (enabled: boolean) => void;
}) {
  const caps: React.ReactNode[] = [];
  if (model.vision) caps.push(<span key="v" className={styles.capBadge + " " + styles.vision} title="Vision"><Eye size={11} strokeWidth={1.8} /></span>);
  if (model.video) caps.push(<span key="vid" className={styles.capBadge + " " + styles.video} title="Video"><Video size={11} strokeWidth={1.8} /></span>);
  if (model.tools) caps.push(<span key="t" className={styles.capBadge + " " + styles.tools} title="Tools"><Wrench size={11} strokeWidth={1.8} /></span>);
  if (model.reasoning) caps.push(<span key="r" className={styles.capBadge + " " + styles.reasoning} title="Reasoning"><Brain size={11} strokeWidth={1.8} /></span>);
  if (model.context_window)
    caps.push(<span key="c" className={styles.capBadge + " " + styles.ctx}>{formatCtx(model.context_window)}</span>);

  return (
    <div className={styles.modelItem}>
      <div className={styles.modelItemIcon}>
        <ProviderIcon id={providerId} size={20} />
      </div>
      <div className={styles.modelItemInfo}>
        <span className={styles.modelItemName}>{model.name || model.id}</span>
        <span className={styles.modelItemId}>{model.id}</span>
      </div>
      <div className={styles.modelCapabilities}>{caps}</div>
      <label className={styles.toggleSwitch}>
        <input
          type="checkbox"
          checked={model.enabled}
          onChange={(e) => onToggle(e.target.checked)}
        />
        <span className={styles.slider} />
      </label>
    </div>
  );
}
