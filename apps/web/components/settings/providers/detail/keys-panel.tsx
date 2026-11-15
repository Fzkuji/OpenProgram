"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronRight, MoreHorizontal, Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { confirmAction } from "@/components/ui/confirm-dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import { MENU_ITEM_STATES, MENU_PANEL, MENU_SEPARATOR, itemCls } from "@/components/ui/menu/menu-styles";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import { normalizeSecretReplacement } from "@/lib/net/auth/secret-replacement";
import { settingsSend } from "@/lib/settings/settings-request";
import type { ProviderStatus } from "@/lib/settings/provider-status.generated";
import { cn } from "@/lib/utils";

import { hostOf } from "../add/data";
import { KeyEntry, USABLE } from "../add/key-entry";
import { BaseUrl } from "../auth/base-url";
import { ProviderLogin } from "../auth/provider-login";
import { keyState, quotaRow } from "../status/model";
import { HealthChip, QuotaRowView } from "../status/parts";
import styles from "../status/status.module.css";
import type { Provider, ProviderAccountView } from "../types";

interface AccountsState {
  accounts: ProviderAccountView[];
  active: string;
  rotation: boolean;
  strategy: string;
  strategies: string[];
  add_mode: "api_key" | "login";
}

const JSON_HEADERS = { "Content-Type": "application/json" };

/** The keys panel of a provider's page (design §5): one row per key or
 *  sign-in with the active radio, name, masked tail and health; Rename,
 *  Replace and Remove behind the row's ⋯; Add key; the rotation choice only
 *  with two or more keys; the same limit rows as the card; Base URL folded
 *  under Advanced. */
export function KeysPanel({ provider, status, onChanged }: {
  provider: Provider;
  status: ProviderStatus | null;
  onChanged: () => void;
}) {
  const { text } = useTranslation();
  const base = `/api/providers/${encodeURIComponent(provider.id)}/accounts`;
  const [state, setState] = useState<AccountsState | null>(null);
  const [failed, setFailed] = useState(false);
  const [adding, setAdding] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [replacing, setReplacing] = useState<string | null>(null);
  const [replacement, setReplacement] = useState("");
  const [replaceError, setReplaceError] = useState("");
  const [advanced, setAdvanced] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch(base);
      if (!r.ok) throw new Error(String(r.status));
      setState((await r.json()) as AccountsState);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [base]);
  useEffect(() => { void load(); }, [load]);

  // The card's "Replace key" lands here: the key in use was rejected, so its
  // replacement field is open once the keys are read.
  const offeredReplace = useRef(false);
  useEffect(() => {
    if (offeredReplace.current || !state || status?.fix?.action !== "replace_key") return;
    offeredReplace.current = true;
    const active = state.accounts.find((a) => a.is_active && a.kind === "api_key")
      ?? state.accounts.find((a) => a.kind === "api_key");
    if (active) setReplacing(active.id);
  }, [state, status?.fix?.action]);

  const send = async (path: string, body: unknown) => {
    const ok = (await settingsSend(`${base}/${path}`, { body, failure: text("Could not change this key. Try again.", "无法更改这个密钥，请重试。") })) !== null;
    await load();
    onChanged();
    return ok;
  };

  const now = Date.now() / 1000;
  const healthOf = (a: ProviderAccountView) => {
    const key = status?.keys.find((k) => k.account === a.id || k.account === a.name || k.credential_id === a.id);
    if (!key) return null;
    const state = keyState(key.status, key.cooldown_until, now);
    return { state, until: state === "rate_limited" ? key.cooldown_until : null, detail: key.last_error };
  };

  async function rename(a: ProviderAccountView) {
    const name = renameValue.trim();
    setRenaming(null);
    if (!name || name === (a.label || a.name)) return;
    await send("rename", { id: a.id, name });
  }

  async function replace(a: ProviderAccountView) {
    const value = normalizeSecretReplacement(replacement, a.masked_key);
    if (value === null) return;
    const r = await fetch(`${base}/${encodeURIComponent(a.id)}/update`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ api_key: value, validate: true }) });
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.ok) {
      setReplacing(null); setReplacement(""); setReplaceError("");
      await load(); onChanged();
    } else {
      setReplaceError(String(d.error || text("Key rejected", "密钥被拒绝")));
    }
  }

  async function remove(a: ProviderAccountView) {
    if (!(await confirmAction({
      title: text(`Remove "${a.label || a.name}"?`, `删除“${a.label || a.name}”？`),
      description: text("Its saved key or sign-in is deleted from this computer.", "这台电脑上保存的密钥或登录信息会被删除。"),
      confirmLabel: text("Remove", "删除"),
      destructive: true,
    }))) return;
    await send("remove", { id: a.id });
  }

  if (!state) {
    return failed ? (
      <section className={styles.panel}>
        <div className={styles.panelHead}>{text("Keys", "密钥")}</div>
        <div className="flex items-center justify-between gap-[10px]">
          <span className={cn(styles.xs, styles.muted)}>{text("Could not load the keys.", "无法加载密钥。")}</span>
          <Button variant="elevated" size="sm" onClick={() => void load()}>{text("Retry", "重试")}</Button>
        </div>
      </section>
    ) : null;
  }

  const accounts = state.accounts ?? [];
  const multi = accounts.length > 1;
  const activeId = accounts.find((a) => a.is_active)?.id ?? "";
  const strategyLabel: Record<string, string> = {
    fill_first: text("In order", "按顺序"),
    round_robin: text("Spread", "均匀"),
    least_used: text("Least used", "最少使用"),
    random: text("Random", "随机"),
  };
  const limits = (status?.quota?.limits ?? []).map((l) => quotaRow(l, now, text));
  const isKey = state.add_mode === "api_key";
  const showAdd = adding || accounts.length === 0;

  const rows = accounts.map((a) => {
    const health = healthOf(a);
    return (
      <div key={a.id} className={styles.keyRow} data-account={a.id}>
        {state.rotation ? (
          <Checkbox checked={a.enabled ?? true} aria-label={text(`Include ${a.label || a.name} in rotation`, `让 ${a.label || a.name} 参与轮换`)}
            onCheckedChange={(checked) => void send("enabled", { id: a.id, enabled: checked === true })} />
        ) : (
          <RadioGroupItem value={a.id} aria-label={text(`Use ${a.label || a.name}`, `使用 ${a.label || a.name}`)} />
        )}
        {renaming === a.id ? (
          <Input autoFocus value={renameValue} aria-label={text("Key name", "密钥名称")} className="h-(--ui-button-h-sm)"
            onChange={(e) => setRenameValue(e.target.value)} onBlur={() => void rename(a)}
            onKeyDown={(e) => { if (e.key === "Enter") void rename(a); if (e.key === "Escape") setRenaming(null); }} />
        ) : (
          <span className={styles.keyName}>{a.label || a.name}</span>
        )}
        {a.kind === "api_key" || a.masked_key ? <span className={styles.keyMask}>{a.has_value ? a.masked_key : text("not set", "未设置")}</span>
          : a.email ? <span className={cn(styles.xs, styles.secondary)}>{a.email}</span> : null}
        <span className="flex-1" />
        {health ? <HealthChip health={health} /> : null}
        <DropdownMenu>
          <HoverTip label={text("Key actions", "密钥操作")}>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-xs" className={styles.keyMenu} aria-label={text(`Actions for ${a.label || a.name}`, `${a.label || a.name} 的操作`)}>
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
          </HoverTip>
          <DropdownMenuContent align="end" className={cn(MENU_PANEL, "min-w-[160px] text-text-primary")}>
            <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={() => { setRenameValue(a.label || a.name); setRenaming(a.id); }}>
              <MenuRow glyph="pen" label={text("Rename", "重命名")} />
            </DropdownMenuItem>
            <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={() => void send(`${encodeURIComponent(a.id)}/validate`, {})}>
              <MenuRow glyph="shield-check" label={text("Check", "检查")} />
            </DropdownMenuItem>
            {a.kind === "api_key" ? (
              <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={() => { setReplacement(""); setReplaceError(""); setReplacing(a.id); }}>
                <MenuRow glyph="restart" label={text("Replace key", "更换密钥")} />
              </DropdownMenuItem>
            ) : null}
            <DropdownMenuSeparator className={MENU_SEPARATOR} />
            <DropdownMenuItem className={cn(itemCls(false, true), MENU_ITEM_STATES)} onSelect={() => void remove(a)}>
              <MenuRow glyph="trash-bin-trash" label={text("Remove", "删除")} danger />
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    );
  });

  return (
    <section className={styles.panel} aria-label={text("Keys", "密钥")}>
      <div className={styles.panelHead}>
        <span>{isKey ? text("Keys", "密钥") : text("Accounts", "账号")}</span>
        <span className="flex-1" />
        {accounts.length ? (
          <Button variant="ghost" size="xs" aria-expanded={adding} onClick={() => setAdding((v) => !v)}>
            {adding ? <X aria-hidden="true" /> : <Plus aria-hidden="true" />}
            {adding ? text("Cancel", "取消") : isKey ? text("Add key", "添加密钥") : text("Sign in", "登录")}
          </Button>
        ) : null}
      </div>
      {accounts.length ? (
        <RadioGroup value={activeId} onValueChange={(id) => void send("use", { id })} className="gap-0" aria-label={text("Key in use", "正在使用的密钥")}>
          {rows}
        </RadioGroup>
      ) : null}
      {replacing ? (() => {
        const a = accounts.find((x) => x.id === replacing);
        return a ? (
          <div className="flex flex-col gap-[6px]">
            <div className="flex items-center gap-[6px]">
              <Input type="password" autoFocus className={styles.keyInput} autoComplete="new-password" aria-label={text("New API key", "新 API 密钥")}
                placeholder={text("Paste the new key", "粘贴新密钥")} value={replacement} onChange={(e) => setReplacement(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") void replace(a); if (e.key === "Escape") setReplacing(null); }} />
              <Button size="sm" disabled={!replacement.trim()} onClick={() => void replace(a)}>{text("Save", "保存")}</Button>
              <Button variant="ghost" size="sm" onClick={() => setReplacing(null)}>{text("Cancel", "取消")}</Button>
            </div>
            {replaceError ? <div className={cn(styles.line, styles.bad)} role="alert">{replaceError}</div> : null}
          </div>
        ) : null;
      })() : null}
      {showAdd ? (
        isKey ? (
          <KeyEntry providerId={provider.id} consoleUrl={status?.console_url ?? null} host={hostOf(provider)} autoFocus
            name={accounts.length ? `key-${accounts.length + 1}` : undefined}
            onStored={(stored) => { if (!stored.check || USABLE.has(stored.check.result)) setAdding(false); void load(); onChanged(); }} />
        ) : (
          <ProviderLogin provider={provider} bare onChanged={() => { setAdding(false); void load(); onChanged(); }} />
        )
      ) : null}
      {multi ? (
        <div className={styles.keyRow}>
          <span className={cn(styles.xs, styles.secondary)}>{text("Rotate", "轮换")}</span>
          <span className="flex-1" />
          <Tabs value={state.rotation ? state.strategy : "off"}
            onValueChange={(v) => void send("rotation", v === "off" ? { enabled: false, strategy: state.strategy } : { enabled: true, strategy: v })}>
            <TabsList aria-label={text("Rotation", "轮换方式")}>
              <TabsTrigger value="off" className="px-2">{text("Off", "关")}</TabsTrigger>
              {(state.strategies?.length ? state.strategies : ["fill_first", "round_robin", "least_used"]).map((s) => (
                <TabsTrigger key={s} value={s} className="px-2">{strategyLabel[s] ?? s}</TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>
      ) : null}
      {limits.length ? (
        <>
          <div className={styles.subhead}>{text("Limits", "额度")}</div>
          {limits.map((row) => <QuotaRowView key={row.key} row={row} />)}
        </>
      ) : null}
      {provider.api_key_env || provider.local ? (
        <>
          <button type="button" className={styles.fold} aria-expanded={advanced} onClick={() => setAdvanced((v) => !v)}>
            <ChevronRight aria-hidden="true" />{text("Advanced · Base URL", "高级 · Base URL")}
          </button>
          {advanced ? <BaseUrl provider={provider} onChanged={onChanged} /> : null}
        </>
      ) : null}
    </section>
  );
}
