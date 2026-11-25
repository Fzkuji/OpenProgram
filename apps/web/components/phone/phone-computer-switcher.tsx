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

import { useEffect, useState, useSyncExternalStore } from "react";
import { Check, ChevronDown } from "lucide-react";

import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { GROUP_LABEL, MENU_ITEM_STATES, MENU_PANEL, itemCls } from "@/components/ui/menu/menu-styles";
import { useTranslation } from "@/lib/i18n";
import { LOCAL_ID, ensureMachines, machineOnline, useMachines } from "@/lib/machines/store";
import { callMobile, haptic, mobileBridge } from "@/lib/mobile/bridge";
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

  const line = (name: string, sub: string, on: boolean) => (
    <>
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
        {here ? (
          <>
            <div className={GROUP_LABEL}>{text("Paired with this phone", "这台手机配对的电脑")}</div>
            {(paired.length ? paired : [{ id: here.id ?? "", name: here.name }]).map((computer) => {
              const current = computer.name === here.name && (!here.id || computer.id === here.id);
              return (
                <DropdownMenuItem
                  key={computer.id || computer.name}
                  className={row}
                  onSelect={() => {
                    haptic("selection");
                    if (current) setShown(LOCAL_ID);
                    else callMobile("selectComputer", { id: computer.id });
                  }}
                >
                  {line(computer.name, current ? status.replace(`${here.name} · `, "") : text("Paired", "已配对"), current && !shownLinked)}
                </DropdownMenuItem>
              );
            })}
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
                {line(machine.name, machineOnline(machine) ? text("Online", "在线") : text("Offline", "离线"), machine.id === shown)}
              </DropdownMenuItem>
            ))}
          </>
        ) : null}
        {here ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem className={row} onSelect={() => callMobile("addComputer")}>
              {text("Pair another computer", "配对另一台电脑")}
            </DropdownMenuItem>
            <DropdownMenuItem className={row} onSelect={() => callMobile("computers")}>
              {text("Manage computers…", "管理电脑…")}
            </DropdownMenuItem>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
