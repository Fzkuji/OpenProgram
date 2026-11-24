"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Brain, Eye, MoreHorizontal, Star, Wrench } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import { MENU_ITEM_STATES, MENU_PANEL, itemCls } from "@/components/ui/menu/menu-styles";
import { SearchInput } from "@/components/ui/search-input";
import { Switch } from "@/components/ui/switch";
import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import { api } from "@/lib/net/api";
import { errorDetailText } from "@/lib/net/error-text";
import { settingsSend } from "@/lib/settings/settings-request";
import { cn } from "@/lib/utils";

import { fmtContext, fmtPrice, fmtUsd } from "./status/model";
import styles from "./status/status.module.css";
import { refreshAgentChip, type Model, type Provider } from "./types";

/** The models table of a provider's page (design §5): on/off, id (its
 *  metadata on hover), context, price per million tokens in, out and cache
 *  read, capabilities, this provider's 30-day spend per model (Included
 *  for a subscription, its list price on hover) and the default star; Fetch models, Enable all and Disable all behind ⋯. */
export function ModelList({
  provider,
  models,
  spend,
  included = false,
  defaultModel,
  onReload,
}: {
  provider: Provider;
  models: Model[];
  /** 30-day spend per model id (list price), from the usage endpoint. */
  spend?: Record<string, number>;
  /** A subscription: spend reads Included, the list price only on hover. */
  included?: boolean;
  /** The global default as "provider/model" or the bare model id. */
  defaultModel?: string | null;
  onReload: () => void;
}) {
  const { text } = useTranslation();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [fetchStatus, setFetchStatus] = useState<string | null>(null);
  const [starred, setStarred] = useState<string | null>(null);
  const enabledCount = models.filter((m) => m.enabled).length;
  const q = search.trim().toLowerCase();
  const matched = q ? models.filter((m) => (m.name || "").toLowerCase().includes(q) || m.id.toLowerCase().includes(q)) : models;
  // The handful in use come first; the catalogue order holds within each group.
  const rows = [...matched].sort((a, b) => Number(b.enabled) - Number(a.enabled));
  const current = starred ?? (defaultModel?.startsWith(`${provider.id}/`) ? defaultModel.slice(provider.id.length + 1) : defaultModel ?? null);

  const changed = () => {
    onReload();
    void queryClient.invalidateQueries({ queryKey: ["models-enabled"] });
    refreshAgentChip();
  };

  async function toggle(modelId: string, enabled: boolean) {
    await settingsSend(`/api/providers/${encodeURIComponent(provider.id)}/models/${encodeURIComponent(modelId)}/toggle`, {
      body: { enabled },
      failure: text("Could not change this model. Try again.", "无法更改这个模型，请重试。"),
    });
    changed();
  }

  async function bulkToggle(enabled: boolean) {
    await Promise.all(models.filter((m) => m.enabled !== enabled).map((m) =>
      settingsSend(`/api/providers/${encodeURIComponent(provider.id)}/models/${encodeURIComponent(m.id)}/toggle`, {
        body: { enabled },
        failure: text("Could not change every model. Try again.", "部分模型未能更改，请重试。"),
      })));
    changed();
  }

  async function makeDefault(m: Model) {
    if (!m.enabled) await toggle(m.id, true);
    await api.switchModel(provider.id, m.id);
    setStarred(m.id);
    changed();
  }

  async function fetchRemote() {
    setFetchStatus(text("Fetching…", "获取中…"));
    try {
      const r = await fetch(`/api/providers/${encodeURIComponent(provider.id)}/fetch-models`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      const d = await r.json() as { fetched?: number; added?: number; error?: unknown };
      if (d.error) { setFetchStatus(text("Failed: ", "失败：") + errorDetailText(d.error, String(r.status))); return; }
      const fetched = Number(d.fetched ?? 0);
      const added = Number(d.added ?? 0);
      setFetchStatus(added > 0
        ? text(`Fetched ${fetched}, added ${added} new`, `已获取 ${fetched} 个，新增 ${added} 个`)
        : text(`Fetched ${fetched} · up to date`, `已获取 ${fetched} 个 · 已是最新`));
      onReload();
    } catch (e) {
      setFetchStatus(text("Failed: ", "失败：") + (e as Error).message);
    }
  }

  const meta = (m: Model) => [
    m.name && m.name !== m.id ? m.name : "",
    m.family ? text(`Family ${m.family}`, `系列 ${m.family}`) : "",
    m.max_tokens ? text(`Output ${fmtContext(m.max_tokens)}`, `输出 ${fmtContext(m.max_tokens)}`) : "",
    m.knowledge_cutoff ? text(`Knowledge ${m.knowledge_cutoff}`, `知识截止 ${m.knowledge_cutoff}`) : "",
    m.release_date ? text(`Released ${m.release_date}`, `发布 ${m.release_date}`) : "",
    m.api ?? "",
  ].filter(Boolean).join(" · ");

  return (
    <section className={styles.panel} aria-label={text("Models", "模型")}>
      <div className={styles.panelHead}>
        <span>{text("Models", "模型")}</span>
        <span className={cn(styles.xs, styles.muted)} data-count>{text(`${enabledCount} of ${models.length} enabled`, `已启用 ${enabledCount} / ${models.length}`)}</span>
        <span className="flex-1" />
        {fetchStatus ? <span className={cn(styles.xs, styles.muted)} role="status">{fetchStatus}</span> : null}
        <SearchInput className="w-[220px]" placeholder={text("Search models", "搜索模型")} aria-label={text("Search models", "搜索模型")} value={search} onChange={setSearch} />
        <DropdownMenu>
          <HoverTip label={text("Model actions", "模型操作")}>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label={text("Model actions", "模型操作")}><MoreHorizontal /></Button>
            </DropdownMenuTrigger>
          </HoverTip>
          <DropdownMenuContent align="end" className={cn(MENU_PANEL, "min-w-[170px] text-text-primary")}>
            {provider.supports_fetch ? (
              <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={() => void fetchRemote()}>
                <MenuRow glyph="refresh" label={text("Fetch models", "获取模型")} />
              </DropdownMenuItem>
            ) : null}
            <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={() => void bulkToggle(true)}>
              <MenuRow label={text("Enable all", "全部启用")} />
            </DropdownMenuItem>
            <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={() => void bulkToggle(false)}>
              <MenuRow label={text("Disable all", "全部停用")} />
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <div className="overflow-x-auto">
        <table className={styles.models}>
          <thead>
            <tr>
              <th aria-label={text("On", "启用")} />
              <th>{text("Model", "模型")}</th>
              <th className={styles.num}>{text("Context", "上下文")}</th>
              <th className={styles.num}>{text("In / Out / Cache $/M", "输入 / 输出 / 缓存 $/M")}</th>
              <th>{text("Can", "能力")}</th>
              <th className={styles.num}>{text("30 days", "30 天")}</th>
              <th aria-label={text("Default", "默认")} />
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => {
              const info = meta(m);
              const isDefault = current === m.id;
              const caps = [
                { on: m.tools !== false && m.tools != null, icon: <Wrench key="t" aria-hidden="true" />, label: text("Tool use", "工具调用") },
                { on: !!m.vision, icon: <Eye key="v" aria-hidden="true" />, label: text("Vision", "视觉") },
                { on: !!m.reasoning, icon: <Brain key="r" aria-hidden="true" />, label: text("Reasoning", "推理") },
              ].filter((c) => c.on);
              return (
                <tr key={m.id} className={cn(!m.enabled && styles.off)} data-model={m.id}>
                  <td><Switch size="sm" checked={m.enabled} onCheckedChange={(v) => void toggle(m.id, v)} aria-label={text(`Enable ${m.id}`, `启用 ${m.id}`)} /></td>
                  <td className={styles.id}>{info ? <HoverTip label={info}><span tabIndex={0}>{m.id}</span></HoverTip> : m.id}</td>
                  <td className={styles.num}>{fmtContext(m.context_window) || "—"}</td>
                  <td className={styles.num}>
                    {m.input_cost == null && m.output_cost == null ? "—"
                      : `${fmtPrice(m.input_cost)} / ${fmtPrice(m.output_cost)} / ${fmtPrice(m.cache_read_cost)}`}
                  </td>
                  <td>
                    <span className={styles.caps}>
                      {caps.map((c) => <HoverTip key={c.label} label={c.label}><span className={styles.on} aria-label={c.label} role="img">{c.icon}</span></HoverTip>)}
                    </span>
                  </td>
                  <td className={cn(styles.num, included && spend?.[m.id] ? styles.secondary : undefined)} data-spend>
                    {!spend?.[m.id] ? "—" : included ? (
                      <HoverTip label={text(`${fmtUsd(spend[m.id])} at API prices`, `按 API 价约 ${fmtUsd(spend[m.id])}`)}>
                        <span tabIndex={0}>{text("Included", "套餐内")}</span>
                      </HoverTip>
                    ) : fmtUsd(spend[m.id])}
                  </td>
                  <td>
                    <HoverTip label={isDefault ? text("Default model", "默认模型") : text("Make default", "设为默认")}>
                      <Button variant="ghost" size="icon-xs" className={cn("text-text-muted", isDefault && "text-(--accent-fill) [&_svg]:fill-current")} aria-pressed={isDefault}
                        aria-label={isDefault ? text(`${m.id} is the default`, `${m.id} 是默认模型`) : text(`Make ${m.id} the default`, `将 ${m.id} 设为默认`)}
                        onClick={() => { if (!isDefault) void makeDefault(m); }}>
                        <Star />
                      </Button>
                    </HoverTip>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
