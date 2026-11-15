"use client";

/**
 * Model selector — the content of the chat model `<AgentBadge />`
 * popover.
 *
 * Lists every model the user enabled in Settings, grouped by provider.
 * With more than one provider the panel lists providers and each opens
 * its models in a DropdownMenuSub, the same submenu the sidebar's
 * conversation menu uses (hover, click or →, Radix's pointer grace);
 * the provider of the current model shows that model's name where the
 * others show a count.
 * Chat picks always pin the current conversation (`/api/model`), using
 * the draft `local_*` id when the backend has not acked yet, on the branch
 * the pane shows (`pickBranchModel`), selected or not.
 *
 * Provider state lives here, not beside the composer
 * (docs/reference/design/providers/provider-management.html#chat): a
 * provider that needs action shows its state word and its row leads to the
 * fix; a window or key limit half used shows a ring of % left; the submenu
 * header carries the provider's quota rows; the menu ends with Manage
 * providers. With no provider connected the menu says so and opens Add
 * provider. The state comes with the models (`providers`) and follows each
 * `provider_quota` frame; opening the menu reads the status route, which
 * also reads quota endpoints older than ten minutes in the background.
 *
 * Positioning / click-outside / portal are handled by the shadcn
 * <DropdownMenu> in `index.tsx`; this file renders its rows.
 */

import { useQuery } from "@tanstack/react-query";
import { ChevronRight, ExternalLink } from "lucide-react";

import { useBoundChat } from "../bound-chat";
import { pickBranchModel } from "@/lib/agents/model-pick";
import { api } from "@/lib/net/api";
import { jsonFetch } from "@/lib/net/fetch-client";
import { updatePendingAgentModel } from "@/lib/agents/start-conversation";
import { useTranslation } from "@/lib/i18n";
import {
  CHECK_SLOT_PAD,
  GROUP_LABEL,
  MENU_ITEM_STATES,
  MENU_NOTE,
  MENU_PANEL,
  MENU_SEPARATOR,
  itemCls,
} from "@/components/ui/menu/menu-styles";
import { MenuCheck } from "@/components/ui/menu/menu-check";
import { SolarIcon } from "@/components/solar-icons";
import { ModelMark } from "../chips/model-mark";
import {
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { showToast } from "@/lib/format-utils/toast";
import type { PickerProvider, ProviderStatusPayload } from "@/lib/settings/provider-status.generated";
import {
  PICKER_LIMITS_QUERY_KEY,
  PICKER_QUERY_KEY,
  PROVIDERS_PAGE,
  addProviderPage,
  balanceLabel,
  fixLink,
  needsAction,
  providerPage,
  quotaRows,
  quotaSummary,
  stateWord,
  type Text,
} from "@/lib/models/provider-state";
import { QuotaRing, QuotaRows, StateWord } from "./provider-quota";

type EnabledModel = Awaited<ReturnType<typeof api.listEnabledModels>>[number];

const fetchStatus = () => jsonFetch<ProviderStatusPayload>("/api/providers/status");

export function AgentSelector({
  currentProvider,
  currentModel,
  onClose,
  onPick,
}: {
  currentProvider?: string;
  currentModel?: string;
  onClose: () => void;
  /** Use the chosen model for something else than this chat's composer
   *  (the spent-quota card re-sends with it). */
  onPick?: (provider: string, model: string) => void;
}) {
  const { sessionId: currentSessionId, chatKey } = useBoundChat();
  const { t, text } = useTranslation();
  const { data, isError: modelsFailed } = useQuery({
    queryKey: PICKER_QUERY_KEY,
    queryFn: api.listPickerModels,
  });
  const models = data?.models;
  const states = data?.providers ?? {};
  // Quota rows for the submenu headers, and whether any provider is
  // connected at all. Reading it also reads stale quota endpoints.
  const { data: status, isError: statusFailed } = useQuery({
    queryKey: PICKER_LIMITS_QUERY_KEY,
    queryFn: fetchStatus,
    staleTime: 60_000,
  });
  const limitsOf = (id: string) => status?.providers.find((p) => p.id === id)?.quota?.limits ?? [];
  // Group headings name the provider as Settings does: the enabled list
  // carries the label, so the rows never show an id that a later fetch renames.
  const providerName = (id: string) => models?.find((m) => m.provider === id)?.providerLabel
    || status?.providers.find((p) => p.id === id)?.label || id;

  async function pick(provider: string, model: string) {
    onClose();
    if (onPick) {
      onPick(provider, model);
      return;
    }
    try {
      let sid: string | undefined =
        currentSessionId ||
        (chatKey && chatKey !== "__new__" ? chatKey : undefined);
      if (!sid) {
        const { useCenterTabs } = await import("@/lib/tabs/center-tabs-store");
        const { newSession } = await import("@/lib/runtime-bridge/session/conversations");
        const tabs = useCenterTabs.getState();
        const existing = tabs.tabs.find(
          (tab: { kind: string; draft?: boolean; sessionId?: string }) =>
            tab.kind === "session" && tab.draft && tab.sessionId,
        );
        sid = existing?.sessionId ?? tabs.openDraftSessionTab();
        if (!sid) return;
        newSession(sid);
      }
      if (await updatePendingAgentModel(sid, { provider, id: model })) return;
      await pickBranchModel(sid, provider, model);
    } catch (e) {
      showToast(t("agent.switch_failed") + String(e), { tone: "error" });
    }
  }

  // Group enabled models by provider, preserving first-seen order.
  const byProvider: { provider: string; models: EnabledModel[] }[] = [];
  for (const m of models ?? []) {
    let group = byProvider.find((g) => g.provider === m.provider);
    if (!group) {
      group = { provider: m.provider, models: [] };
      byProvider.push(group);
    }
    group.models.push(m);
  }

  const isActive = (m: EnabledModel) =>
    currentProvider === m.provider &&
    (currentModel === m.id || currentModel === `${m.provider}:${m.id}`);
  const modelRows = (list: EnabledModel[]) =>
    list.map((m) => (
      <ModelRow key={m.id} model={m} active={isActive(m)} onPick={() => pick(m.provider, m.id)} />
    ));
  // The submenu header: the provider's state, quota rows and balance.
  const header = (id: string, withName: boolean) => (
    <ProviderHeader name={withName ? providerName(id) : null} id={id} state={states[id]} limits={limitsOf(id)} text={text} />
  );

  const noProvider = !!status && status.providers.length === 0;
  const body = !models ? (
    // The read failed: say so rather than show an empty menu.
    modelsFailed ? <div className={MENU_NOTE} data-picker-error>{text("Could not load models.", "未能读取模型。")}</div> : null
  ) : models.length === 0 ? (
    noProvider ? (
      // Nothing connected: say so, and open Add provider (the Providers
      // page's empty state offers the common providers and any env keys).
      <>
        <div className={MENU_NOTE}>{text("No model provider connected.", "还没有接入模型服务商。")}</div>
        <DropdownMenuItem asChild className={cn(itemCls(false), MENU_ITEM_STATES, "no-underline")}>
          <a href={addProviderPage()} onClick={onClose} data-picker-add-provider>
            <SolarIcon name="add-circle" size={16} fit="fill" aria-hidden="true" />
            <span className="flex-1 truncate">{text("Add provider", "添加服务商")}</span>
          </a>
        </DropdownMenuItem>
      </>
    ) : status || statusFailed ? (
      <div className={MENU_NOTE}>
        {t("agent.no_enabled_models")}{" "}
        <a href={PROVIDERS_PAGE} onClick={onClose} className="text-accent-blue no-underline">
          {t("agent.enable_models")} →
        </a>
      </div>
    ) : null
  ) : byProvider.length === 1 ? (
    // One provider has nothing to choose between: its models directly.
    <SingleProvider
      id={byProvider[0].provider}
      name={providerName(byProvider[0].provider)}
      state={states[byProvider[0].provider]}
      header={header(byProvider[0].provider, false)}
      text={text}
      onClose={onClose}
    >
      {modelRows(byProvider[0].models)}
    </SingleProvider>
  ) : (
    // One grid for every provider row: the model names start after the
    // longest provider name, so the columns never overlap row to row.
    <div className="grid grid-cols-[max-content_minmax(0,1fr)_auto]">
      {byProvider.map((group) => (
        <ProviderRow
          key={group.provider}
          id={group.provider}
          name={providerName(group.provider)}
          state={states[group.provider]}
          current={group.models.find(isActive)?.name}
          count={group.models.length}
          header={header(group.provider, true)}
          text={text}
          onClose={onClose}
        >
          {modelRows(group.models)}
        </ProviderRow>
      ))}
    </div>
  );

  return (
    <>
      {/* Grammar-A header naming the dimension. */}
      <div className={GROUP_LABEL}>{text("Models", "模型")}</div>
      {/* Nothing until the list arrives, or the empty-state note flashes. */}
      {body}
      {models && !noProvider ? (
        <>
          <div className={MENU_SEPARATOR} />
          <DropdownMenuItem asChild className={cn(itemCls(false), MENU_ITEM_STATES, "no-underline")}>
            <a href={PROVIDERS_PAGE} onClick={onClose} data-picker-manage>
              <SolarIcon name="settings-minimalistic" size={16} fit="fill" aria-hidden="true" />
              {/* A plain link to Settings, not a submenu: no chevron. */}
              <span className="flex-1 truncate">{text("Manage providers", "管理服务商")}</span>
            </a>
          </DropdownMenuItem>
        </>
      ) : null}
    </>
  );
}

/** The hint a row that needs action carries on its control. */
function fixTip(state: PickerProvider, text: Text): string {
  switch (state.state) {
    case "not_configured": return text("Add a key or sign in", "添加 key 或登录");
    case "signed_out": return text("Sign in again", "重新登录");
    case "no_credit": return text("Top up, then try again", "充值后再试");
    case "invalid_key": return text("Replace the key", "更换 key");
    case "not_running": return text("Start the local server", "启动本地服务");
    default: return "";
  }
}

/** What a provider row shows right of its name: the ring when half a
 *  window is used, then the state word, or the current model / count.
 *  Inline on the row's text baseline, so the smaller text sits on the
 *  label's line; the model name truncates first. */
function RowTrail({ state, current, count, text }: {
  state?: PickerProvider; current?: string; count: number; text: Text;
}) {
  const word = stateWord(state, text);
  const ring = state?.quota?.ring && !word ? state.quota : null;
  return (
    <span
      className={cn(
        "truncate justify-self-end pl-6 text-fs-xs",
        ring ? "max-w-[186px]" : "max-w-[164px]",
        current ? "text-text-secondary" : "text-text-muted",
      )}
    >
      {ring ? <QuotaRing quota={ring} stale={state?.stale} tip={quotaSummary(ring, text)} className="mr-1.5 align-[-4px]" /> : null}
      {word ? <StateWord label={word.label} tone={word.tone} /> : current ?? count}
    </span>
  );
}

function ProviderRow({ id, name, state, current, count, header, text, onClose, children }: {
  id: string;
  name: string;
  state?: PickerProvider;
  current?: string;
  count: number;
  header: React.ReactNode;
  text: Text;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const rowCls = cn(itemCls(false), MENU_ITEM_STATES, "col-span-3 grid grid-cols-subgrid items-baseline gap-0");
  const label = (
    <span>
      {/* Inline in the label's line, so the row keeps its text baseline. */}
      <ModelMark provider={id} className="mr-2 align-[-3px]" />
      {name}
    </span>
  );
  const trail = <RowTrail state={state} current={current} count={count} text={text} />;
  // A provider that needs action leads straight to its fix. A spent
  // balance with a billing page offers that page and the provider's own.
  if (state && needsAction(state)) {
    const fix = fixLink(state);
    if (!fix.external) {
      return (
        <DropdownMenuItem asChild className={cn(rowCls, "no-underline")}>
          <a href={fix.href} onClick={onClose} data-tip={fixTip(state, text)} data-picker-fix={state.state}>
            {label}
            {trail}
            {/* The chevron's slot, centred like the chevron: on the text
                baseline the empty box stood 1px above the line. */}
            <span className="ml-1.5 size-4 self-center" aria-hidden="true" />
          </a>
        </DropdownMenuItem>
      );
    }
    return (
      <DropdownMenuSub>
        <DropdownMenuSubTrigger className={rowCls} data-picker-fix={state.state}>
          {label}
          {trail}
          <ChevronRight className="menu-row-chevron ml-1.5 size-4 self-center text-text-muted" aria-hidden="true" />
        </DropdownMenuSubTrigger>
        <DropdownMenuSubContent className={cn(MENU_PANEL, "text-text-primary")}>
          {header}
          <FixRows id={id} href={fix.href} text={text} onClose={onClose} />
        </DropdownMenuSubContent>
      </DropdownMenuSub>
    );
  }
  return (
    <DropdownMenuSub>
      <DropdownMenuSubTrigger className={rowCls}>
        {label}
        {trail}
        <ChevronRight className="menu-row-chevron ml-1.5 size-4 self-center text-text-muted" aria-hidden="true" />
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent className={cn(MENU_PANEL, "text-text-primary")}>
        {header}
        {children}
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  );
}

/** Top up (the provider's billing page, outside the app) and the
 *  provider's page in Settings. */
function FixRows({ id, href, text, onClose }: { id: string; href: string; text: Text; onClose: () => void }) {
  return (
    <>
      <DropdownMenuItem asChild className={cn(itemCls(false), MENU_ITEM_STATES, "no-underline")}>
        <a href={href} target="_blank" rel="noreferrer" onClick={onClose} data-picker-top-up>
          <span className="flex-1 truncate">{text("Top up", "充值")}</span>
          <ExternalLink className="size-3.5 shrink-0 text-text-muted" aria-hidden="true" />
        </a>
      </DropdownMenuItem>
      <DropdownMenuItem asChild className={cn(itemCls(false), MENU_ITEM_STATES, "no-underline")}>
        <a href={providerPage(id)} onClick={onClose}>
          <span className="flex-1 truncate">{text("Open provider", "打开服务商")}</span>
        </a>
      </DropdownMenuItem>
    </>
  );
}

/** One provider: its name (with its state), quota rows, the fix when it
 *  needs one, then its models. */
function SingleProvider({ id, name, state, header, text, onClose, children }: {
  id: string;
  name: string;
  state?: PickerProvider;
  header: React.ReactNode;
  text: Text;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const word = stateWord(state, text);
  const ring = state?.quota?.ring && !word ? state.quota : null;
  const fix = state && needsAction(state) ? fixLink(state) : null;
  return (
    <>
      <div className={GROUP_LABEL}>
        <span className="flex-1 truncate">{name}</span>
        {ring ? <QuotaRing quota={ring} stale={state?.stale} tip={quotaSummary(ring, text)} /> : null}
        {word ? <StateWord label={word.label} tone={word.tone} /> : null}
      </div>
      {header}
      {fix && state ? (
        fix.external ? (
          <FixRows id={id} href={fix.href} text={text} onClose={onClose} />
        ) : (
          <DropdownMenuItem asChild className={cn(itemCls(false), MENU_ITEM_STATES, "no-underline")}>
            <a href={fix.href} onClick={onClose} data-picker-fix={state.state}>
              <span className="flex-1 truncate">{fixTip(state, text)}</span>
              <ChevronRight className="menu-row-chevron size-4 shrink-0 text-text-muted" aria-hidden="true" />
            </a>
          </DropdownMenuItem>
        )
      ) : null}
      {fix ? <div className={MENU_SEPARATOR} /> : null}
      {children}
    </>
  );
}

/** The submenu header: name, then the quota rows (Codex's `label [bar]
 *  NN% · reset`) or the balance; nothing when the provider reports none. */
function ProviderHeader({ name, id, state, limits, text }: {
  name: string | null;
  id: string;
  state?: PickerProvider;
  limits: Parameters<typeof quotaRows>[0];
  text: Text;
}) {
  const rows = quotaRows(limits, text);
  const word = stateWord(state, text);
  const balance = !rows.some((r) => r.left == null) && state?.balance ? state.balance : null;
  if (!rows.length && !word && !balance) return null;
  return (
    <>
      {name ? (
        <div className={GROUP_LABEL}>
          <ModelMark provider={id} />
          <span className="flex-1 truncate">{name}</span>
          {word ? <StateWord label={word.label} tone={word.tone} /> : null}
        </div>
      ) : null}
      <QuotaRows rows={rows} />
      {balance ? (
        <div className={cn(MENU_NOTE, "flex gap-2")} data-picker-balance>
          <span>{balance.label || text("Balance", "余额")}</span>
          <span className="tabular-nums text-text-secondary">{balanceLabel(balance)}</span>
        </div>
      ) : null}
      <div className={MENU_SEPARATOR} />
    </>
  );
}

function ModelRow({ model: m, active, onPick }: { model: EnabledModel; active: boolean; onPick: () => void }) {
  return (
    <DropdownMenuItem
      onSelect={onPick}
      // 选中不铺底色（hover 是唯一底色），选中态只靠右侧勾。
      className={cn(itemCls(false), MENU_ITEM_STATES)}
    >
      {/* Claude 实测：元数据（badge/能力/上下文窗）紧贴名字
          左侧成簇，不推到右缘——右缘只留勾位。 */}
      <span className="flex min-w-0 flex-1 items-center gap-[6px]">
        <span className="truncate">{m.name}</span>
        <CapIcons caps={m.capabilities} />
        {m.context ? (
          <span className="shrink-0 text-fs-2xs text-text-muted">
            {fmtCtx(m.context)}
          </span>
        ) : null}
      </span>
      {active ? <MenuCheck /> : <span className={CHECK_SLOT_PAD} />}
    </DropdownMenuItem>
  );
}

/** Capability icons — vision / video / tools / reasoning. `ctx` is
 *  dropped here (the context window already shows as a number). Solar
 *  glyphs, same roles the legacy `_CAP_ICONS` used in providers.js. */
function CapIcons({ caps }: { caps: string[] }) {
  const shown = caps.filter((c) => c !== "ctx");
  if (shown.length === 0) return null;
  return (
    <span className="flex shrink-0 items-center gap-[5px] text-text-muted">
      {shown.map((c) => {
        const icon = CAP_ICON[c];
        return icon ? (
          <span key={c} className="flex" data-tip={c[0].toUpperCase() + c.slice(1)}>
            {icon}
          </span>
        ) : null;
      })}
    </span>
  );
}

const CAP_ICON: Record<string, React.ReactNode> = {
  vision: <SolarIcon motionPreset="none" name="eye" size={14} />,
  video: <SolarIcon motionPreset="none" name="videocamera" size={14} />,
  // The briefcase fills its 24-unit box (y 2–21) while eye and camera
  // span 4–20, so at 14px it looked bigger and higher than its
  // neighbours; 12px gives it the same visual height and the same edges.
  tools: <SolarIcon motionPreset="none" name="case-minimalistic" size={12} />,
  reasoning: <SolarIcon motionPreset="none" name="lightbulb-bolt" size={14} />,
};

/** Compact context-window label: 200000 → "200k", 1048576 → "1M". */
function fmtCtx(n: number): string {
  if (n >= 1_000_000) return `${Math.round(n / 1_000_000)}M`;
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n);
}
