"use client";

/**
 * LLM Providers settings (design: docs/reference/design/providers/provider-management.html).
 *
 * /settings/providers lists the connected providers as cards or compact
 * rows (the switch is remembered), one card or row per company with a
 * section per way it is connected, with Routing beside it; with nothing
 * connected it is the picker of popular providers. Add provider opens the
 * three-step dialog. /settings/providers/<id> is that provider's page.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { LayoutGrid, List, Plus, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ManageEmptyState } from "@/components/ui/manage-page";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import { pushPath } from "@/lib/nav/shallow-nav";
import { invalidate } from "@/lib/prefs/settings-cache";
import { setProvidersView, useProvidersView, type ProvidersView } from "@/lib/prefs/providers-view";
import type { FixAction } from "@/lib/settings/provider-status.generated";
import { settingsSend } from "@/lib/settings/settings-request";
import { cn } from "@/lib/utils";

import { SettingsPage } from "../settings-ui";
import { AddProviderDialog, type AddStart } from "./add/add-provider-dialog";
import { CATALOGUE_URL, choiceOf, useAddData } from "./add/data";
import { PickProvider } from "./add/pick";
import { Detail } from "./detail";
import { AddCredentialDialog } from "./detail/add-credential-dialog";
import { failingCredential } from "./detail/credential-model";
import type { CredentialFix } from "./detail/credentials-section";
import { RoutingOverview } from "./routing-overview";
import { Masonry } from "./status/masonry";
import { companyGroups, estimateCardHeight, fmtAgo, updatedAt } from "./status/model";
import { CompanyCard, ProviderCard } from "./status/provider-card";
import { ProviderRows } from "./status/provider-rows";
import styles from "./status/status.module.css";
import { useProviderStatus } from "./status/use-provider-status";
import { refreshAgentChip } from "./types";

// Re-export ApiKey for the search-providers page (the only other consumer).
export { ApiKey } from "./auth/api-key";
export type { Provider, Model } from "./types";

type Segment = "providers" | "routing";

export function ProvidersSection({ initialProviderId }: { initialProviderId?: string } = {}) {
  const { t, text } = useTranslation();
  const queryClient = useQueryClient();
  const { payload, error, refreshing, reload, refresh, refreshOne } = useProviderStatus();
  const { detected, detectedFailed, catalogue, reload: reloadCatalogue } = useAddData();
  const view = useProvidersView();
  const [segment, setSegment] = useState<Segment>("providers");
  const [dialog, setDialog] = useState<{ open: boolean; start: AddStart }>({ open: false, start: { step: "pick" } });
  // Cleared after a removal so the page falls back to the list before the URL catches up.
  const [left, setLeft] = useState<string | null>(null);
  const selectedId = initialProviderId && initialProviderId !== left ? initialProviderId : null;
  // A card's Sign in again or Replace key opens the page on that credential.
  const [fix, setFix] = useState<(CredentialFix & { provider: string }) | null>(null);
  const [adding, setAdding] = useState<{ open: boolean; provider: string }>({ open: false, provider: "" });

  const openProvider = useCallback((id: string, action?: FixAction) => {
    setLeft(null);
    const keys = payload?.providers.find((p) => p.id === id)?.keys ?? [];
    setFix(action === "sign_in" || action === "replace_key" || action === "top_up"
      ? { provider: id, action, credential: failingCredential(keys, action) }
      : null);
    pushPath(`/settings/providers/${encodeURIComponent(id)}`);
  }, [payload]);
  const showList = useCallback(() => { pushPath("/settings/providers"); }, []);
  const addCredential = useCallback((id: string) => setAdding({ open: true, provider: id }), []);
  const openAdd = (start: AddStart = { step: "pick" }) => setDialog({ open: true, start });
  const connect = (id: string) => {
    const p = catalogue.find((c) => c.id === id);
    if (p) openAdd({ step: "connect", choice: choiceOf(p, detected) });
    else openProvider(id);
  };

  // ?add=1 (or ?add=<provider>) opens Add provider on arrival — the model
  // picker and the "no model" notice link here — and leaves the address clean.
  const catalogueReady = catalogue.length > 0;
  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("add");
    if (!wanted) return;
    if (wanted !== "1" && !catalogueReady) return;
    const url = new URL(window.location.href);
    url.searchParams.delete("add");
    window.history.replaceState(window.history.state, "", url);
    if (wanted === "1") openAdd();
    else connect(wanted);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once per arrival
  }, [catalogueReady]);

  const reloadAll = useCallback(() => {
    invalidate(CATALOGUE_URL);
    void reload(true);
    void reloadCatalogue(true);
    void queryClient.invalidateQueries({ queryKey: ["models-enabled"] });
    refreshAgentChip();
  }, [reload, reloadCatalogue, queryClient]);

  async function toggleProvider(id: string, enabled: boolean) {
    await settingsSend(`/api/providers/${encodeURIComponent(id)}/toggle`, {
      body: { enabled },
      failure: text("Could not change this provider. Try again.", "无法更改这个服务商，请重试。"),
    });
    reloadAll();
  }

  const now = Date.now() / 1000;
  const connected = payload?.providers ?? [];
  const selected = selectedId ? catalogue.find((p) => p.id === selectedId) ?? null : null;
  const updated = payload ? updatedAt(connected, payload.generated_at) : 0;

  const dialogEl = (
    <AddProviderDialog
      open={dialog.open}
      start={dialog.start}
      detected={detected}
      detectedFailed={detectedFailed}
      onRetryDetected={() => void reloadCatalogue(true)}
      catalogue={catalogue}
      onOpenChange={(open) => setDialog((d) => ({ ...d, open }))}
      onConnected={reloadAll}
      onOpenProvider={(id) => { setDialog((d) => ({ ...d, open: false })); reloadAll(); openProvider(id); }}
    />
  );
  const addingProvider = catalogue.find((p) => p.id === adding.provider) ?? null;
  const addingStatus = connected.find((p) => p.id === adding.provider) ?? null;
  const credentialDialog = addingProvider ? (
    <AddCredentialDialog
      open={adding.open}
      onOpenChange={(open) => setAdding((a) => ({ ...a, open }))}
      provider={addingProvider}
      intent={{ mode: "add", prefer: addingStatus?.billing === "subscription" ? "login" : "key" }}
      consoleUrl={addingStatus?.console_url ?? null}
      onDone={reloadAll}
    />
  ) : null;

  if (selectedId) {
    return (
      <SettingsPage title={t("settings.tab.providers")}>
        {selected ? (
          <Detail
            key={selected.id}
            provider={selected}
            status={connected.find((p) => p.id === selected.id) ?? null}
            fix={fix?.provider === selected.id ? fix : null}
            onBack={showList}
            onRefresh={() => refreshOne(selected.id)}
            onToggle={(en) => void toggleProvider(selected.id, en)}
            onChanged={reloadAll}
            onDeleted={() => { setLeft(selected.id); reloadAll(); showList(); }}
          />
        ) : null}
        {dialogEl}
      </SettingsPage>
    );
  }

  const empty = payload !== null && connected.length === 0;
  const actions = (
    <div className="flex flex-wrap items-center justify-end gap-[8px]">
      {payload && !empty ? <span className={styles.updated}>{text("Updated ", "更新于 ")}{fmtAgo(updated, now, text)}</span> : null}
      {!empty ? (
        <HoverTip label={text("Read every sign-in and quota again", "重新读取所有登录和额度")}>
          <Button variant="ghost" size="icon-sm" aria-label={text("Refresh", "刷新")} disabled={refreshing} onClick={() => void refresh()}>
            <RefreshCw className={cn(refreshing && styles.spinning)} />
          </Button>
        </HoverTip>
      ) : null}
      {segment === "providers" && !empty ? (
        <Tabs value={view} onValueChange={(v) => setProvidersView(v as ProvidersView)}>
          <TabsList aria-label={text("View", "视图")}>
            <HoverTip label={text("Cards", "卡片")}>
              <TabsTrigger value="cards" className="px-2" aria-label={text("Cards", "卡片")}><LayoutGrid /></TabsTrigger>
            </HoverTip>
            <HoverTip label={text("Rows", "列表")}>
              <TabsTrigger value="rows" className="px-2" aria-label={text("Rows", "列表")}><List /></TabsTrigger>
            </HoverTip>
          </TabsList>
        </Tabs>
      ) : null}
      <Button size="sm" onClick={() => openAdd()}><Plus aria-hidden="true" />{text("Add provider", "添加服务商")}</Button>
    </div>
  );

  return (
    <SettingsPage
      title={
        <span className="flex items-center gap-3">
          {t("settings.tab.providers")}
          {!empty ? (
            <Tabs value={segment} onValueChange={(v) => setSegment(v as Segment)}>
              <TabsList aria-label={text("Section", "分区")}>
                <TabsTrigger value="providers" className="px-3">{text("Providers", "服务商")}</TabsTrigger>
                <TabsTrigger value="routing" className="px-3">{text("Routing", "路由")}</TabsTrigger>
              </TabsList>
            </Tabs>
          ) : null}
        </span>
      }
      actions={actions}
    >
      {error && !payload ? (
        <div role="alert">
          <ManageEmptyState compact title={text("Couldn't load the providers", "无法读取服务商")} description={error}
            action={<Button variant="elevated" size="sm" onClick={() => void reload(true)}>{text("Retry", "重试")}</Button>} />
        </div>
      ) : null}
      {empty ? (
        <div className="flex flex-col gap-[14px] w-full max-w-[640px] mx-auto mt-[24px]" data-empty>
          <h3 className={styles.emptyTitle}>{text("Connect a model provider", "连接一个模型服务商")}</h3>
          <PickProvider
            detected={detected}
            detectedFailed={detectedFailed}
            onRetry={() => void reloadCatalogue(true)}
            catalogue={catalogue}
            onPick={(choice) => openAdd({ step: "connect", choice })}
            onUseEnv={(row) => openAdd({ step: "env", row })}
            onCustom={() => openAdd({ step: "custom" })}
          />
        </div>
      ) : segment === "routing" ? (
        <RoutingOverview providers={catalogue} onSelectProvider={openProvider} />
      ) : payload ? (
        <div className={styles.listWrap} data-view={view}>
          {view === "rows" ? (
            <ProviderRows providers={connected} now={now} onOpen={openProvider} onConnect={connect} />
          ) : (
            <Masonry items={companyGroups(connected)} keyOf={(g) => g.family} estimate={estimateCardHeight}>
              {(group) => group.ways.length > 1 ? (
                <CompanyCard group={group} now={now} onOpen={openProvider} onConnect={connect} onAddCredential={addCredential} />
              ) : (
                <ProviderCard p={group.ways[0]} now={now} onOpen={openProvider} onConnect={connect} onAddCredential={addCredential} />
              )}
            </Masonry>
          )}
        </div>
      ) : null}
      {dialogEl}
      {credentialDialog}
    </SettingsPage>
  );
}
