"use client";

/** A card of the provider list: a company with every credential of each
 *  of its ways as one row. A row is three fixed parts, the same on every
 *  card: who it is (the account, the key's name or last four, over its way
 *  and plan), its limits as up to three rings (each over its name and when
 *  it resets), and one last column that holds either an amount while the
 *  credential works (credits, a balance, 30 days of spend) or why it does
 *  not and when that changes. A row opens the provider's page; a danger
 *  cell fixes what it names (sign in, replace the key, top up). */
import { memo, useState } from "react";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { MENU_ITEM_STATES, MENU_PANEL, itemCls } from "@/components/ui/menu/menu-styles";
import { HoverTip } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import type { FixAction, ProviderStatus } from "@/lib/settings/provider-status.generated";
import { cn } from "@/lib/utils";

import { providerLogo, ProviderIcon } from "../icons/provider-icon";
import { cardRows, foldedSummary, shownRows, wayTitle, type CardRow, type CompanyGroup, type Ring } from "./model";
import styles from "./status.module.css";

type Handlers = {
  now: number;
  /** Open the provider's page; a fix names the action, and the credential it is for. */
  onOpen: (id: string, fix?: FixAction, credential?: string | null) => void;
  onConnect: (id: string) => void;
  onAddCredential: (id: string) => void;
};

const R = 8.5;
const LENGTH = 2 * Math.PI * R;

/** What is left of one limit: a full ring is all of it, a faint red one
 *  none, a dashed one a limit that could not be read. */
function RingMark({ ring }: { ring: Ring }) {
  const spent = ring.left != null && ring.left <= 0;
  return (
    <svg className={cn(styles.ring, "mb-0.5 block")} data-tone={spent ? "spent" : ring.left == null ? "unread" : ring.tone} width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
      <circle className={styles.ringTrack} cx="11" cy="11" r={R} />
      {ring.left != null && !spent ? (
        <circle className={styles.ringFill} cx="11" cy="11" r={R} strokeDasharray={`${(LENGTH * ring.left).toFixed(2)} ${LENGTH.toFixed(2)}`} transform="rotate(-90 11 11)" />
      ) : null}
    </svg>
  );
}

const FIX_TIP: Record<FixAction, [string, string]> = {
  sign_in: ["Sign in again", "重新登录"],
  replace_key: ["Replace this key", "更换这把密钥"],
  top_up: ["Top up", "充值"],
  start_server: ["How to start it", "如何启动"],
  connect: ["Connect", "接入"],
};

function CredentialRow({ row, now, onOpen }: { row: CardRow } & Pick<Handlers, "now" | "onOpen">) {
  const { text } = useTranslation();
  const { p, end } = row;
  const open = () => onOpen(p.id);
  const fix = end.fix;
  const fixHere = () => {
    if (!fix) return;
    if (fix === "top_up" && p.fix?.action === "top_up" && p.fix.url) window.open(p.fix.url, "_blank", "noopener,noreferrer");
    else onOpen(p.id, fix, row.credential);
  };
  const endCell = (
    <span className={styles.rowEnd} data-tone={end.tone} data-end>
      <span className={styles.rowTop}>{end.top}</span>
      <span className={styles.rowSub}>{end.bottom}</span>
    </span>
  );
  const body = (
    <div className={styles.credRow} data-provider={p.id} data-credential={row.credential ?? undefined} data-stale={row.stale || undefined} data-off={row.off || undefined}
      role="button" tabIndex={0} onClick={open} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } }}>
      <span className={styles.rowWho}>
        <span className={styles.rowTop}>{row.inUse ? <span className={styles.inUse} aria-label={text("In use", "在用")} /> : null}{row.name}</span>
        <span className={styles.rowSub} data-ways={row.listsWays || undefined}>{row.sub}</span>
      </span>
      <span className={cn(styles.rings, "grid grid-cols-[repeat(3,44px)] justify-end gap-2")} data-rings={row.rings.length}>
        {row.rings.map((ring) => (
          <HoverTip key={ring.key} label={ring.tip}>
            <span className="flex min-w-0 flex-col items-center" data-limit={ring.key}>
              <RingMark ring={ring} />
              <span className={styles.ringLabel}>{ring.label}</span>
              <span className={styles.ringUnder}>{ring.under}</span>
            </span>
          </HoverTip>
        ))}
      </span>
      {fix ? (
        <HoverTip label={text(...FIX_TIP[fix])}>
          <Button variant="ghost" className={cn(styles.rowFix, "h-auto rounded-sm px-[6px] py-[4px] font-normal")} onClick={(e) => { e.stopPropagation(); fixHere(); }}>{endCell}</Button>
        </HoverTip>
      ) : endCell}
    </div>
  );
  return row.updated ? <HoverTip label={row.updated}>{body}</HoverTip> : body;
}

/** The provider whose logo stands for a company: its first way that has one. */
export function companyIcon(group: CompanyGroup): string {
  return group.ways.find((p) => providerLogo(p.id))?.id ?? group.family;
}

/** The one +: Add key or Add account for a provider, a menu of the ways for a company. */
function AddButton({ group, onAdd }: { group: CompanyGroup; onAdd: (id: string) => void }) {
  const { text } = useTranslation();
  const ways = group.ways.filter((p) => p.billing !== "local");
  if (!ways.length) return null;
  const nameOf = (p: ProviderStatus) => (p.takes_keys ? text("Add key", "添加密钥") : text("Add account", "添加账号"));
  const empty = ways.every((p) => p.keys.length === 0);
  const trigger = (label: string, onClick?: () => void) => (
    <Button variant={empty ? "default" : "ghost"} size="icon-xs" aria-label={label} className="ml-auto flex-none" onClick={(e) => { e.stopPropagation(); onClick?.(); }}>
      <Plus aria-hidden="true" />
    </Button>
  );
  if (ways.length === 1) return <HoverTip label={nameOf(ways[0])}>{trigger(nameOf(ways[0]), () => onAdd(ways[0].id))}</HoverTip>;
  return (
    <DropdownMenu>
      <HoverTip label={text("Add", "添加")}><DropdownMenuTrigger asChild>{trigger(text("Add", "添加"))}</DropdownMenuTrigger></HoverTip>
      <DropdownMenuContent align="end" sideOffset={4} className={cn(MENU_PANEL, "min-w-[200px] text-text-primary")}>
        {ways.map((p) => (
          <DropdownMenuItem key={p.id} className={cn(itemCls(false), MENU_ITEM_STATES)} onSelect={() => onAdd(p.id)}>
            {wayTitle(p, text)}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** A company's card (or a provider's, when it is one way of its own). */
export const CompanyCard = memo(function CompanyCard({ group, now, onOpen, onAddCredential }: { group: CompanyGroup } & Handlers) {
  const { text } = useTranslation();
  const [unfolded, setUnfolded] = useState(false);
  const rows = cardRows(group, now, text);
  const { shown, hidden } = unfolded ? { shown: rows, hidden: [] } : shownRows(rows);
  const model = group.ways.find((p) => p.default_model)?.default_model ?? "";
  return (
    <Card size="sm" className={styles.card} data-family={group.family} data-provider={group.ways.length === 1 ? group.ways[0].id : undefined}>
      <div className="flex min-w-0 items-center gap-2 pr-3 pb-1 pl-4">
        <ProviderIcon id={companyIcon(group)} size={20} />
        <span className={styles.cardTitle} data-slot="card-title">{group.label}</span>
        {model ? <span className={styles.cardModel}>{model}</span> : null}
        <AddButton group={group} onAdd={onAddCredential} />
      </div>
      <div className={cn(styles.credRows, "flex min-w-0 flex-col gap-2 px-3 pb-3")}>
        {shown.map((row) => <CredentialRow key={row.key} row={row} now={now} onOpen={onOpen} />)}
        {hidden.length ? (
          <Button variant="ghost" className={cn(styles.credRow, "grid h-auto font-normal whitespace-normal")} data-folded onClick={() => setUnfolded(true)}>
            <span className={styles.rowWho}>
              <span className={styles.rowTop}>{text(`${hidden.length} more`, `还有 ${hidden.length} 个`)}</span>
              <span className={styles.rowSub}>{foldedSummary(hidden)}</span>
            </span>
          </Button>
        ) : null}
      </div>
    </Card>
  );
});
