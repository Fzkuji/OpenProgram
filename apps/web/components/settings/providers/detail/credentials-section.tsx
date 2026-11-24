"use client";

/**
 * A provider's credentials (docs/reference/design/providers/provider-management.html,
 * "Credentials"): its keys and signed-in accounts in the order they are
 * used, each with its name, the "In use" mark on the one a new conversation
 * takes, its masked tail and today's use or why it stopped, its quota bars,
 * its status, and the one fix it needs inline (Replace, Sign in again, Top
 * up). Each row's ⋯ menu renames, checks, replaces, sets aside, moves and
 * removes it; only Remove asks first. Rows are dragged into a new order by
 * their grip (a button a keyboard can pick up too: Space, then each arrow
 * press moves it one row, Space drops it; a screen reader hears each row's
 * name), or moved with Move up / Move down. With two or more keys, the header picks
 * how requests are spread. With none, the empty state adds one or links to
 * where keys are made.
 *
 * Which ways in a provider has comes from the login-method registry alone
 * (`auth.methods`): its `api_key` entry means the provider takes pasted
 * keys (Add key), every other entry is a way to sign in. A row whose
 * cooldown runs out reads Ready at that moment: the section reads the list
 * again when the earliest hold ends.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  DndContext, KeyboardCode, KeyboardSensor, PointerSensor, useDraggable, useDroppable, useSensor, useSensors,
  type Announcements, type DragEndEvent, type KeyboardCoordinateGetter,
} from "@dnd-kit/core";
import { ExternalLink, GripVertical, KeyRound, MoreHorizontal, Plus, UserRound } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { confirmAction } from "@/components/ui/confirm-dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ManageEmptyState, ManageRow } from "@/components/ui/manage-page";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import { MENU_ITEM_STATES, MENU_PANEL, MENU_SEPARATOR, itemCls } from "@/components/ui/menu/menu-styles";
import { Progress } from "@/components/ui/progress";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { HoverTip } from "@/components/ui/tooltip";
import { SettingsHeading } from "@/components/settings/settings-ui";
import { showToast } from "@/lib/format-utils/toast";
import { useTranslation } from "@/lib/i18n";
import { errorDetailText } from "@/lib/net/error-text";
import { refreshSettings, revalidateSettings, setCachedSettings, useSettingsData } from "@/lib/prefs/settings-cache";
import type { FixAction } from "@/lib/settings/provider-status.generated";
import { cn } from "@/lib/utils";

import { planLabel, quotaRow } from "../status/model";
import type { CredentialList, CredentialRow, CredentialStrategy, LoginMethod, Provider } from "../types";
import { AddCredentialDialog, type CredentialIntent } from "./add-credential-dialog";
import {
  CREDENTIALS_CHANGED, KEY_METHOD, credentialBadge, credentialName, credentialUrl, credentialsUrl, failureLine, isSignIn,
  maskedTail, nextExpiry, rowMeters, strategyLabel, todayLine, usableNow,
} from "./credential-model";

type Text = (en: string, zh: string) => string;

/** The fix a card or the page header sent here: open that row's dialog. */
export interface CredentialFix {
  action: FixAction;
  credential: string | null;
}

/** The keyboard moves a picked-up row one row per arrow press: to the top
 *  of the row above or below the one it is over now. */
export const rowByRow: KeyboardCoordinateGetter = (event, { currentCoordinates, context }) => {
  const step = event.code === KeyboardCode.Down ? 1 : event.code === KeyboardCode.Up ? -1 : 0;
  if (!step) return undefined;
  const tops = [...context.droppableRects.values()].map((r) => r.top).sort((a, b) => a - b);
  if (!tops.length) return undefined;
  const here = tops.reduce((best, top, i) => (Math.abs(top - currentCoordinates.y) < Math.abs(tops[best] - currentCoordinates.y) ? i : best), 0);
  const to = Math.max(0, Math.min(tops.length - 1, here + step));
  event.preventDefault();
  return { x: currentCoordinates.x, y: tops[to] };
};

async function send(method: "POST" | "PATCH" | "DELETE", url: string, failure: string, body?: unknown): Promise<boolean> {
  try {
    const r = await fetch(url, {
      method,
      ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    });
    if (r.ok) return true;
    const d = await r.json().catch((): { error?: unknown } => ({})) as { error?: unknown };
    showToast(errorDetailText(d.error, failure), { tone: "error" });
  } catch {
    showToast(failure, { tone: "error" });
  }
  return false;
}

function sectionDescription(list: CredentialList, provider: Provider, signIns: boolean, text: Text): string {
  if (!list.credentials.length) {
    return signIns
      ? text(`Sign in to use ${provider.label}'s models.`, `登录后即可使用 ${provider.label} 的模型。`)
      : text(`${provider.label} needs an API key to run its models.`, `${provider.label} 需要 API 密钥才能运行它的模型。`);
  }
  if (signIns) return text("Signed-in accounts, used top to bottom.", "已登录的账号，从上往下使用。");
  switch (list.strategy) {
    case "evenly": return text("Requests take turns across the keys; a chat keeps its key until it fails.", "请求在各密钥间轮流；一次对话会一直用同一个密钥，直到它出错。");
    case "least_used": return text("Each new chat takes the key used least; a chat keeps its key until it fails.", "每次新对话用使用最少的密钥；对话会一直用它，直到它出错。");
    default: return text("Used top to bottom; a chat keeps its key until it fails.", "从上往下使用；一次对话会一直用同一个密钥，直到它出错。");
  }
}

/** One quota window as a label and percent over a bar ("5 hours  62%"). */
function Meter({ label, used }: { label: string; used: number }) {
  const pct = Math.round(used * 100);
  return (
    <div className="flex w-[92px] flex-none flex-col gap-[3px]" data-meter>
      <div className="flex justify-between text-fs-2xs text-text-secondary">
        <span className="truncate">{label}</span><span>{pct}%</span>
      </div>
      <Progress value={pct} tone={pct >= 95 ? "danger" : pct >= 80 ? "warning" : "normal"} />
    </div>
  );
}

function CredentialItem({
  row, index, total, list, provider, now, renaming, onRename, onStartRename, onCancelRename,
  onCheck, onReplace, onRelogin, onEnabled, onMove, onRemove,
}: {
  row: CredentialRow;
  index: number;
  total: number;
  list: CredentialList;
  provider: Provider;
  now: number;
  renaming: boolean;
  onRename: (label: string) => void;
  onStartRename: () => void;
  onCancelRename: () => void;
  onCheck: () => void;
  onReplace: () => void;
  onRelogin: () => void;
  onEnabled: (enabled: boolean) => void;
  onMove: (to: number) => void;
  onRemove: () => void;
}) {
  const { text } = useTranslation();
  const drag = useDraggable({ id: row.id });
  const drop = useDroppable({ id: row.id });
  const shownName = credentialName(row, text);
  const [draft, setDraft] = useState(row.label || shownName);
  const signIn = isSignIn(row);
  const badge = credentialBadge(row, now, text);
  const meters = rowMeters(row.limits).map((l) => ({ key: l.limit_key, label: quotaRow(l, now, text).label, used: 1 - (l.left_fraction ?? 1) }));
  const failure = row.enabled ? failureLine(row, provider.label, text, now) : "";
  const failureTone = row.status === "cooling" || row.status === "needs_login" ? "text-(--warning-soft)" : "text-(--danger-soft)";
  const usage = todayLine(row, signIn, text);
  const lead = signIn ? planLabel(row.identity.plan) : row.env_var ? `$${row.env_var}` : "";
  const via = row.signin_name || provider.label;
  // "Plus · signed in with ChatGPT · 12 requests today".
  const quiet = signIn && !failure
    ? (index === 0 ? text(`signed in with ${via}`, `已用 ${via} 登录`)
      : !usage.length ? text("takes over when the one above is used up", "上一个用完后接上") : "")
    : "";
  const parts: React.ReactNode[] = [
    ...(!signIn && row.key_tail ? [<span key="tail" className="font-mono text-fs-xs">{maskedTail(row.key_tail)}</span>] : []),
    ...(lead ? [<span key="lead" className={cn(row.env_var && "font-mono text-fs-xs")}>{lead}</span>] : []),
    ...(failure ? [<span key="fail" className={failureTone}>{failure}</span>]
      : [...(quiet ? [<span key="quiet">{quiet}</span>] : []), ...usage.map((u) => <span key={u}>{u}</span>)]),
  ];
  const style = drag.transform ? { transform: `translate3d(0, ${drag.transform.y}px, 0)` } : undefined;
  const commit = () => { const label = draft.trim(); if (label && label !== (row.label || shownName)) onRename(label); else onCancelRename(); };
  const item = (glyph: Parameters<typeof MenuRow>[0]["glyph"], label: string, onSelect: () => void, opts: { disabled?: boolean; danger?: boolean } = {}) => (
    <DropdownMenuItem className={cn(itemCls(false, opts.danger), MENU_ITEM_STATES)} disabled={opts.disabled} onSelect={onSelect}>
      <MenuRow glyph={glyph} label={label} danger={opts.danger} />
    </DropdownMenuItem>
  );

  return (
    <div
      ref={(node) => { drag.setNodeRef(node); drop.setNodeRef(node); }}
      style={style}
      data-credential={row.id}
      data-status={row.enabled ? row.status : "off"}
      data-dragging={drag.isDragging || undefined}
      data-over={drop.isOver && !drag.isDragging ? "" : undefined}
      className={cn("relative bg-card data-[dragging]:z-10 data-[dragging]:shadow-raised data-[over]:shadow-[inset_0_2px_0_var(--primary)]", !row.enabled && "opacity-60")}
    >
      <ManageRow
        className="min-h-14"
        handle={(
          <Button variant="ghost" size="icon-xs" {...drag.listeners} {...drag.attributes}
            aria-label={text(`Reorder ${shownName}`, `调整“${shownName}”的顺序`)}
            className="cursor-grab text-text-muted active:cursor-grabbing" data-grip>
            <GripVertical />
          </Button>
        )}
        name={renaming ? (
          <Input autoFocus value={draft} aria-label={text("Name", "名称")} className="h-(--ui-button-h-sm) max-w-60"
            onChange={(e) => setDraft(e.target.value)} onBlur={commit}
            onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") onCancelRename(); }} />
        ) : shownName}
        meta={row.id === list.active ? <Badge variant="accent">{text("In use", "使用中")}</Badge> : null}
        description={parts.length ? (
          <span className="flex min-w-0 items-center gap-2 truncate">
            {parts.map((p, i) => (
              <span key={i} className="contents">
                {i > 0 ? <span className="text-text-muted">·</span> : null}{p}
              </span>
            ))}
          </span>
        ) : undefined}
        actions={(
          <>
            {meters.length ? (
              <div className="flex gap-3.5">{meters.map((m) => <Meter key={m.key} label={m.label} used={m.used} />)}</div>
            ) : null}
            {!(meters.length && usableNow(row, now)) ? (
              <Badge variant={badge.tone} dot data-state={row.enabled ? row.status : "off"}>{badge.label}</Badge>
            ) : null}
            {row.enabled && row.status === "invalid_key" && !signIn && !row.read_only ? (
              <Button variant="elevated" size="xs" onClick={onReplace}>{text("Replace", "更换")}</Button>
            ) : row.enabled && row.status === "needs_login" ? (
              <Button variant="elevated" size="xs" onClick={onRelogin}>{text("Sign in again", "重新登录")}</Button>
            ) : row.enabled && row.status === "quota" && list.auth.billing_url ? (
              <Button asChild variant="link" size="xs">
                <a href={list.auth.billing_url} target="_blank" rel="noopener noreferrer">
                  {text("Top up", "充值")}<ExternalLink data-icon="inline-end" aria-hidden="true" />
                </a>
              </Button>
            ) : null}
            <DropdownMenu>
              <HoverTip label={text("More", "更多")}>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon-xs" aria-label={text(`Actions for ${shownName}`, `${shownName} 的操作`)}>
                    <MoreHorizontal />
                  </Button>
                </DropdownMenuTrigger>
              </HoverTip>
              <DropdownMenuContent align="end" className={cn(MENU_PANEL, "min-w-[196px] text-text-primary")}>
                {!row.read_only ? item("pen", text("Rename", "重命名"), () => { setDraft(row.label || shownName); onStartRename(); }) : null}
                {item("refresh", text("Check now", "立即检查"), onCheck)}
                {signIn ? item("restart", text("Sign in again", "重新登录"), onRelogin)
                  : !row.read_only ? item("transfer-horizontal", text("Replace key", "更换密钥"), onReplace) : null}
                {row.enabled
                  ? item("pause-circle", text("Don't use for now", "暂不使用"), () => onEnabled(false))
                  : item("play-circle", text("Use again", "恢复使用"), () => onEnabled(true))}
                <DropdownMenuSeparator className={MENU_SEPARATOR} />
                {item("sort-from-bottom-to-top", text("Move up", "上移"), () => onMove(index - 1), { disabled: index === 0 })}
                {item("sort-from-top-to-bottom", text("Move down", "下移"), () => onMove(index + 1), { disabled: index === total - 1 })}
                <DropdownMenuSeparator className={MENU_SEPARATOR} />
                {item("trash-bin-trash", text("Remove…", "删除…"), onRemove, { danger: true })}
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        )}
      />
    </div>
  );
}

/** What a screen reader hears while a row is moved: names and places, never
 *  ids. A row over its own place (the pickup itself) says nothing more than
 *  the pickup did. */
export function moveAnnouncements(rows: CredentialRow[], text: Text): Announcements {
  const nameOf = (id: string | number | undefined) => {
    const at = rows.findIndex((r) => r.id === id);
    return at < 0 ? "" : `${credentialName(rows[at], text)} (${at + 1}/${rows.length})`;
  };
  const moving = (id: string | number) => credentialName(rows.find((r) => r.id === id) ?? rows[0], text);
  return {
    onDragStart: ({ active }) => text(`Picked up ${nameOf(active.id)}.`, `已拿起 ${nameOf(active.id)}。`),
    onDragOver: ({ active, over }) => over && over.id !== active.id
      ? text(`${moving(active.id)} is over ${nameOf(over.id)}.`, `${moving(active.id)} 在 ${nameOf(over.id)} 上方。`)
      : undefined,
    onDragEnd: ({ active, over }) => over && over.id !== active.id
      ? text(`Moved to the place of ${nameOf(over.id)}.`, `已移到 ${nameOf(over.id)} 的位置。`)
      : text("Put back where it was.", "放回原处。"),
    onDragCancel: () => text("Moving cancelled; the order is unchanged.", "已取消移动，顺序未变。"),
  };
}

/** A provider's ways in from the login-method registry (`auth.methods`):
 *  its pasted-key entry, and the ways to sign in. */
export function waysIn(methods: LoginMethod[]): { keyEntry: LoginMethod | null; loginMethods: LoginMethod[] } {
  return {
    keyEntry: methods.find((m) => m.id === KEY_METHOD) ?? null,
    loginMethods: methods.filter((m) => m.id !== KEY_METHOD),
  };
}

export function CredentialsSection({
  provider, consoleUrl = null, fix = null, onChanged,
}: {
  provider: Provider;
  /** Where keys are made, when the status knows it before the list does. */
  consoleUrl?: string | null;
  /** The fix that opened this page (a card's Sign in again or Replace key). */
  fix?: CredentialFix | null;
  onChanged: () => void;
}) {
  const { text } = useTranslation();
  // Kept for the visit: reopening a provider shows its credentials at once and
  // reads them again behind; a failed read keeps them and retries quietly.
  const listUrl = credentialsUrl(provider.id);
  const list = useSettingsData<CredentialList>(listUrl) ?? null;
  const setList = useCallback((next: (previous: CredentialList | undefined) => CredentialList | undefined) =>
    setCachedSettings<CredentialList>(listUrl, next), [listUrl]);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [dialog, setDialog] = useState<{ open: boolean; intent: CredentialIntent }>({ open: false, intent: { mode: "add" } });
  const root = useRef<HTMLElement>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: rowByRow }));

  const load = useCallback(async () => { await refreshSettings(listUrl); }, [listUrl]);
  // Sign-ins and quotas change on their own: every visit reads the list
  // again behind the kept one, joining a read already on its way.
  useEffect(() => { void revalidateSettings(listUrl); }, [listUrl]);
  useEffect(() => {
    const onChange = (event: Event) => {
      const changed = (event as CustomEvent<{ provider?: string }>).detail?.provider;
      if (!changed || changed === provider.id) void load();
    };
    window.addEventListener(CREDENTIALS_CHANGED, onChange);
    return () => window.removeEventListener(CREDENTIALS_CHANGED, onChange);
  }, [load, provider.id]);

  const done = useCallback(() => { void load(); onChanged(); }, [load, onChanged]);
  const failure = text("Could not change this credential. Try again.", "无法更改这个凭据，请重试。");
  const act = async (method: "POST" | "PATCH" | "DELETE", url: string, body?: unknown) => {
    const ok = await send(method, url, failure, body);
    if (ok) done();
    return ok;
  };

  // A card's fix lands here once: the row it names (or the first that needs
  // it) opens its dialog, and the section scrolls into view.
  const handled = useRef(false);
  useEffect(() => {
    if (handled.current || !list || !fix) return;
    handled.current = true;
    root.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    const want = fix.action === "sign_in" ? "needs_login" : fix.action === "replace_key" ? "invalid_key" : fix.action === "top_up" ? "quota" : "";
    const row = list.credentials.find((c) => c.id === fix.credential) ?? list.credentials.find((c) => c.status === want);
    if (fix.action === "sign_in") {
      setDialog({ open: true, intent: row ? { mode: "relogin", id: row.id, name: credentialName(row, text) } : { mode: "add", prefer: "login" } });
    } else if (fix.action === "replace_key" && row && !isSignIn(row)) {
      setDialog({ open: true, intent: { mode: "replace", id: row.id, name: credentialName(row, text) } });
    }
  }, [fix, list, text]);

  const [now, setNow] = useState(() => Date.now() / 1000);
  const rows = list?.credentials ?? [];
  // The earliest hold that runs out re-reads the list then, so its badge,
  // the In use mark and the menu catch up without a reload.
  const expiry = nextExpiry(rows, now);
  useEffect(() => {
    if (expiry == null) return;
    const timer = window.setTimeout(() => { setNow(Date.now() / 1000); void load(); },
      Math.max(0, expiry * 1000 - Date.now()) + 500);
    return () => window.clearTimeout(timer);
  }, [expiry, load]);
  useEffect(() => { setNow(Date.now() / 1000); }, [list]);
  const { keyEntry, loginMethods } = waysIn(list?.auth.methods ?? []);
  const takesKeys = !!keyEntry;
  const signIns = rows.length ? rows.every(isSignIn) : !takesKeys && loginMethods.length > 0;
  const keysUrl = list?.auth.keys_url ?? consoleUrl;
  const openAdd = (prefer?: "key" | "login") => setDialog({ open: true, intent: { mode: "add", prefer } });

  async function move(from: number, to: number) {
    if (to < 0 || to >= rows.length || from === to) return;
    const order = rows.map((r) => r.id);
    const [id] = order.splice(from, 1);
    order.splice(to, 0, id);
    setList((l) => (l ? { ...l, credentials: order.map((x) => l.credentials.find((c) => c.id === x)!) } : l));
    if (!(await act("POST", `${credentialsUrl(provider.id)}/reorder`, { order }))) void load();
  }

  const announcements = moveAnnouncements(rows, text);
  const screenReaderInstructions = {
    draggable: text("Press Space to pick up a row, the up and down arrows to move it one place, Space to drop it, Escape to cancel.",
      "按空格拿起一行，按上下方向键每次移动一位，再按空格放下，按 Esc 取消。"),
  };

  function onDragEnd(event: DragEndEvent) {
    const over = event.over?.id;
    if (!over || over === event.active.id) return;
    void move(rows.findIndex((r) => r.id === event.active.id), rows.findIndex((r) => r.id === over));
  }

  async function remove(row: CredentialRow) {
    const shown = credentialName(row, text);
    if (!(await confirmAction({
      title: text(`Remove "${shown}"?`, `删除“${shown}”？`),
      description: row.env_var
        ? text(`It is read from $${row.env_var}; unset it there to remove it.`, `它读自 $${row.env_var}，需要在那里删除。`)
        : text("Its saved key or sign-in is deleted from this computer.", "这台电脑上保存的密钥或登录信息会被删除。"),
      confirmLabel: text("Remove", "删除"),
      destructive: true,
    }))) return;
    await act("DELETE", credentialUrl(provider.id, row.id));
  }

  const addButtons = (
    <>
      {rows.length > 1 && !signIns && list ? (
        <HoverTip label={text("How requests are spread", "请求如何分配")}>
          <ToggleGroup type="single" variant="segmented" size="xs" value={list.strategy}
            aria-label={text("How requests are spread", "请求如何分配")}
            onValueChange={(v) => { if (v) void act("POST", `${credentialsUrl(provider.id)}/strategy`, { strategy: v as CredentialStrategy }); }}>
            {(list.strategies.length ? list.strategies : ["in_order", "evenly", "least_used"] as CredentialStrategy[]).map((s) => (
              <ToggleGroupItem key={s} value={s}>{strategyLabel(s, text)}</ToggleGroupItem>
            ))}
          </ToggleGroup>
        </HoverTip>
      ) : null}
      {takesKeys ? (
        <Button variant="elevated" size="sm" onClick={() => openAdd("key")}>
          <Plus data-icon="inline-start" aria-hidden="true" />{text("Add key", "添加密钥")}
        </Button>
      ) : null}
      {loginMethods.length ? (
        <Button variant="elevated" size="sm" onClick={() => openAdd("login")}>
          <UserRound data-icon="inline-start" aria-hidden="true" />
          {rows.some(isSignIn) ? text("Sign in with another account", "登录另一个账号") : text("Sign in", "登录")}
        </Button>
      ) : null}
    </>
  );

  return (
    <section ref={root} className="flex flex-col gap-3" aria-label={text("Credentials", "凭据")} data-credentials-section>
      <SettingsHeading
        title={text("Credentials", "凭据")}
        description={list ? sectionDescription(list, provider, signIns, text) : undefined}
        action={list ? addButtons : undefined}
      />
      <Card className="gap-0 py-0">
        <CardContent className="px-0">
        {!list ? null : rows.length === 0 ? (
          <ManageEmptyState
            dense
            icon={signIns ? <UserRound /> : <KeyRound />}
            title={signIns ? text("Not signed in", "还没登录") : text("No key yet", "还没有密钥")}
            description={signIns
              ? text(`Sign in with your ${provider.label} account. You can add more accounts later; they are used in order.`, `用你的 ${provider.label} 账号登录。之后可以再加账号，按顺序使用。`)
              : text(`Create a key in the ${provider.label} console, then paste it here. You can add more keys later; they are used in order.`, `先在 ${provider.label} 控制台创建密钥，再粘贴到这里。之后可以再加密钥，按顺序使用。`)}
            action={(
              <>
                <Button size="sm" onClick={() => openAdd(signIns ? "login" : "key")}>
                  <Plus data-icon="inline-start" aria-hidden="true" />{signIns ? text("Sign in", "登录") : text("Add key", "添加密钥")}
                </Button>
                {!signIns && keysUrl ? (
                  <Button asChild variant="link" size="sm">
                    <a href={keysUrl} target="_blank" rel="noopener noreferrer">
                      {text("Get a key", "获取密钥")}<ExternalLink data-icon="inline-end" aria-hidden="true" />
                    </a>
                  </Button>
                ) : null}
              </>
            )}
          />
        ) : (
          <DndContext sensors={sensors} onDragEnd={onDragEnd} autoScroll={false}
            accessibility={{ announcements, screenReaderInstructions }}>
            <div className="divide-y divide-border">
              {rows.map((row, i) => (
                <CredentialItem
                  key={row.id}
                  row={row} index={i} total={rows.length} list={list} provider={provider} now={now}
                  renaming={renaming === row.id}
                  onStartRename={() => setRenaming(row.id)}
                  onCancelRename={() => setRenaming(null)}
                  onRename={(label) => { setRenaming(null); void act("PATCH", credentialUrl(provider.id, row.id), { label }); }}
                  onCheck={() => void act("POST", `${credentialUrl(provider.id, row.id)}/check`)}
                  onReplace={() => setDialog({ open: true, intent: { mode: "replace", id: row.id, name: credentialName(row, text) } })}
                  onRelogin={() => setDialog({ open: true, intent: { mode: "relogin", id: row.id, name: credentialName(row, text) } })}
                  onEnabled={(enabled) => void act("PATCH", credentialUrl(provider.id, row.id), { enabled })}
                  onMove={(to) => void move(i, to)}
                  onRemove={() => void remove(row)}
                />
              ))}
            </div>
          </DndContext>
        )}
        </CardContent>
      </Card>
      {rows.length > 1 && !signIns ? (
        <p className="px-0.5 text-fs-xs text-text-muted">{text("Drag a row, or use Move up / Move down, to change the order.", "拖动一行，或用“上移 / 下移”调整顺序。")}</p>
      ) : null}
      <AddCredentialDialog
        open={dialog.open}
        onOpenChange={(open) => setDialog((d) => ({ ...d, open }))}
        provider={provider}
        intent={dialog.intent}
        consoleUrl={keysUrl}
        onDone={done}
      />
    </section>
  );
}
