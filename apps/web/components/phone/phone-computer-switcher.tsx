"use client";

/**
 * Which computer the phone shows, from the computer's name at the top of
 * Projects. Two kinds of computer are listed: those paired with this
 * phone (each its own connection, switched by the app), and those the
 * paired computer reaches through its own pairing (Settings → Machines,
 * an OpenProgram on another computer), whose projects it already lists.
 * One is shown at a time, as Happy and Paseo show one machine's sessions:
 * the second kind is a filter on the same list.
 */

import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { Check, ChevronDown, Link2, Monitor, Plus, Settings2 } from "lucide-react";

import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { GROUP_LABEL, MENU_ITEM_STATES, MENU_PANEL, itemCls } from "@/components/ui/menu/menu-styles";
import { useTranslation } from "@/lib/i18n";
import { LOCAL_ID, ensureMachines, machineOnline, useMachines } from "@/lib/machines/store";
import { callMobile, haptic, mobileBridge } from "@/lib/mobile/bridge";
import { nativeMenus, openNativeMenu, type NativeMenuItem } from "@/lib/mobile/native-menu";
import { cn } from "@/lib/utils";

const KEY = "openprogram.mobile.machine:";
const listeners = new Set<() => void>();

function storageKey(): string {
  return KEY + (mobileBridge()?.computer.id ?? mobileBridge()?.computer.name ?? "browser");
}

function readShown(): string {
  try {
    return localStorage.getItem(storageKey()) || LOCAL_ID;
  } catch {
    return LOCAL_ID;
  }
}

function setShown(id: string): void {
  try {
    if (id === LOCAL_ID) localStorage.removeItem(storageKey());
    else localStorage.setItem(storageKey(), id);
  } catch {
    /* storage blocked: the choice lasts until the page reloads */
  }
  listeners.forEach((listener) => listener());
}

/** The machine whose projects Projects shows: this computer, or one it is linked to. */
export function useShownMachine(): string {
  const shown = useSyncExternalStore(
    (listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    readShown,
    () => LOCAL_ID,
  );
  const linked = useLinkedComputers();
  // A linked computer that was removed falls back to this one.
  return shown === LOCAL_ID || linked.some((machine) => machine.id === shown) ? shown : LOCAL_ID;
}

/** Computers the paired one reaches through their own OpenProgram. */
export function useLinkedComputers() {
  const items = useMachines((s) => s.items);
  useEffect(() => { void ensureMachines(); }, []);
  return items.filter((machine) => machine.id !== LOCAL_ID && machine.enabled && machine.connection?.kind === "openprogram");
}

function usePaired() {
  const [list, setList] = useState(() => mobileBridge()?.computers ?? []);
  useEffect(() => {
    const read = () => setList(mobileBridge()?.computers ?? []);
    window.addEventListener("openprogram-mobile-computers", read);
    return () => window.removeEventListener("openprogram-mobile-computers", read);
  }, []);
  return list;
}

export function PhoneComputerSwitcher({ status }: { status: string }) {
  const { text } = useTranslation();
  const bridge = mobileBridge();
  const here = bridge?.computer;
  const paired = usePaired();
  const linked = useLinkedComputers();
  const shown = useShownMachine();
  const shownLinked = linked.find((machine) => machine.id === shown);
  const row = cn(itemCls(false), MENU_ITEM_STATES, "min-h-11 gap-3");
  const label = shownLinked
    ? text(`${shownLinked.name} · through ${here?.name ?? "this computer"}`, `${shownLinked.name} · 经 ${here?.name ?? "这台电脑"}`)
    : status;

  if (!here && !linked.length) {
    return <span className="phone-home-computer-label">{label}</span>;
  }

  const pairedList = here ? (paired.length ? paired : [{ id: here.id ?? "", name: here.name }]) : [];
  const isCurrent = (computer: { id: string; name: string }) =>
    !!here && computer.name === here.name && (!here.id || computer.id === here.id);
  const pickPaired = (computer: { id: string; name: string }) => {
    haptic("selection");
    if (isCurrent(computer)) setShown(LOCAL_ID);
    else callMobile("selectComputer", { id: computer.id });
  };
  const pairedSub = (computer: { id: string; name: string }) =>
    isCurrent(computer) ? status.replace(`${here?.name} · `, "") : text("Paired", "已配对");
  const linkedSub = (machine: (typeof linked)[number]) =>
    machineOnline(machine) ? text("Online", "在线") : text("Offline", "离线");

  // In the phone app iOS draws the menu, as Files does from its title.
  if (nativeMenus()) {
    const items: NativeMenuItem[] = [];
    if (pairedList.length) items.push({
      title: text("Paired with this phone", "这台手机配对的电脑"),
      inline: true,
      children: pairedList.map((computer) => ({
        title: computer.name, subtitle: pairedSub(computer), icon: "desktopcomputer",
        checked: isCurrent(computer) && !shownLinked, run: () => pickPaired(computer),
      })),
    });
    if (linked.length) items.push({
      title: text(`Through ${here?.name ?? "this computer"}`, `经 ${here?.name ?? "这台电脑"} 连接`),
      inline: true,
      children: linked.map((machine) => ({
        title: machine.name, subtitle: linkedSub(machine), icon: "link",
        checked: machine.id === shown, run: () => { haptic("selection"); setShown(machine.id); },
      })),
    });
    if (here) items.push({
      title: "",
      inline: true,
      children: [
        { title: text("Pair Another Computer", "配对另一台电脑"), icon: "plus", run: () => callMobile("addComputer") },
        { title: text("Manage Computers", "管理电脑"), icon: "gearshape", run: () => callMobile("computers") },
      ],
    });
    return (
      <button type="button" className="phone-home-computer-label" aria-label={text(`Computer: ${label}`, `电脑：${label}`)}
        onClick={(event) => openNativeMenu(event.currentTarget, items)}>
        <span className="truncate">{label}</span>
        <ChevronDown size={14} strokeWidth={2} className="phone-home-computer-more" aria-hidden="true" />
      </button>
    );
  }

  const line = (name: string, sub: string, on: boolean, icon: ReactNode) => (
    <>
      <span className="phone-sheet-icon" aria-hidden="true">{icon}</span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate">{name}</span>
        <span className="truncate text-fs-xs text-text-muted">{sub}</span>
      </span>
      {on ? <Check size={16} className="shrink-0 text-[var(--accent-blue)]" aria-hidden="true" /> : null}
    </>
  );

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button type="button" className="phone-home-computer-label" aria-label={text(`Computer: ${label}`, `电脑：${label}`)}>
          <span className="truncate">{label}</span>
          <ChevronDown size={14} strokeWidth={2} className="phone-home-computer-more" aria-hidden="true" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className={cn(MENU_PANEL, "min-w-64")} align="start" onCloseAutoFocus={(event) => event.preventDefault()}>
        <div className="phone-sheet-head">{text("Computers", "电脑")}</div>
        {here ? (
          <>
            <div className={GROUP_LABEL}>{text("Paired with this phone", "这台手机配对的电脑")}</div>
            {pairedList.map((computer) => (
              <DropdownMenuItem key={computer.id || computer.name} className={row} onSelect={() => pickPaired(computer)}>
                {line(computer.name, pairedSub(computer), isCurrent(computer) && !shownLinked, <Monitor size={18} />)}
              </DropdownMenuItem>
            ))}
          </>
        ) : null}
        {linked.length ? (
          <>
            <div className={GROUP_LABEL}>{text(`Through ${here?.name ?? "this computer"}`, `经 ${here?.name ?? "这台电脑"} 连接`)}</div>
            {linked.map((machine) => (
              <DropdownMenuItem
                key={machine.id}
                className={row}
                onSelect={() => { haptic("selection"); setShown(machine.id); }}
              >
                {line(machine.name, linkedSub(machine), machine.id === shown, <Link2 size={18} />)}
              </DropdownMenuItem>
            ))}
          </>
        ) : null}
        {here ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem className={cn(row, "text-[var(--theme-accent)]")} onSelect={() => callMobile("addComputer")}>
              <span className="phone-sheet-icon" aria-hidden="true"><Plus size={18} /></span>
              {text("Pair another computer", "配对另一台电脑")}
            </DropdownMenuItem>
            <DropdownMenuItem className={row} onSelect={() => callMobile("computers")}>
              <span className="phone-sheet-icon" aria-hidden="true"><Settings2 size={18} /></span>
              {text("Manage computers", "管理电脑")}
            </DropdownMenuItem>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
