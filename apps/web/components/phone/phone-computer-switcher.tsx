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

import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { Check, Laptop, Link2, Plus, Settings2 } from "lucide-react";
import { Link, List, ListItem } from "framework7-react";

import { useTranslation } from "@/lib/i18n";
import { LOCAL_ID, ensureMachines, machineOnline, useMachines } from "@/lib/machines/store";
import { callMobile, haptic, mobileBridge } from "@/lib/mobile/bridge";

import { PhonePortal } from "./phone-portal";

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

/** The computer as a bar button: a laptop with its connection as a dot
 *  (green when connected). The button itself grows into the list of
 *  computers (ButtonMorph below), the one shown checked with its state
 *  under its name. */
export function PhoneComputerSwitcher({ status, online }: { status: string; online: boolean; slot?: string }) {
  const { text } = useTranslation();
  const bridge = mobileBridge();
  const here = bridge?.computer;
  const paired = usePaired();
  const linked = useLinkedComputers();
  const shown = useShownMachine();
  const shownLinked = linked.find((machine) => machine.id === shown);
  const [open, setOpen] = useState(false);
  // The bar's glass around the button, which the menu grows out of; the
  // anchor has no box (display: contents), the link inside it sits in it.
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const anchor = useCallback((el: HTMLSpanElement | null) => {
    const link = el?.querySelector<HTMLElement>(".link");
    if (link) setTarget(link.closest<HTMLElement>(".left") ?? link);
  }, []);
  const label = shownLinked
    ? text(`${shownLinked.name} · through ${here?.name ?? "this computer"}`, `${shownLinked.name} · 经 ${here?.name ?? "这台电脑"}`)
    : status;

  const mark = (
    <>
      <Laptop size={22} aria-hidden="true" />
      <span className="phone-computer-dot" data-online={online ? "" : undefined} aria-hidden="true" />
    </>
  );
  if (!here && !linked.length) {
    return <span className="phone-computer-button" role="img" aria-label={label}>{mark}</span>;
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
  // A computer reached through the paired one says so under its name.
  const linkedSub = (machine: (typeof linked)[number]) =>
    `${text(`Through ${here?.name ?? "this computer"}`, `经 ${here?.name ?? "这台电脑"}`)} · ${machineOnline(machine) ? text("Online", "在线") : text("Offline", "离线")}`;

  const pick = (run: () => void) => () => { setOpen(false); run(); };
  const check = (on: boolean) => (on ? <Check size={20} strokeWidth={2.4} aria-hidden="true" /> : null);

  return (
    <>
      {/* It grows as the finger lands, as iOS's bar menus do: no pressed
          flash of the button first (no-active-state), no wait for the tap
          to end. */}
      <span ref={anchor} className="phone-computer-anchor" onPointerDown={() => setOpen(true)}>
        <Link
          iconOnly
          className="phone-computer-button no-active-state"
          aria-label={text(`Computer: ${label}`, `电脑：${label}`)}
          aria-expanded={open}
          onClick={() => setOpen(true)}
        >
          {mark}
        </Link>
      </span>
      <PhonePortal>
        {target ? <ButtonMorph open={open} from={target} mark={mark} onClose={() => setOpen(false)}>
          <List>
            {pairedList.map((computer) => (
              <ListItem
                key={computer.id || computer.name}
                link="#"
                noChevron
                onClick={pick(() => pickPaired(computer))}
                title={computer.name}
                footer={pairedSub(computer)}
              >
                {/* The same laptop as the button the menu grew from. */}
                <span slot="media"><Laptop size={22} aria-hidden="true" /></span>
                <span slot="after">{check(isCurrent(computer) && !shownLinked)}</span>
              </ListItem>
            ))}
            {linked.map((machine) => (
              <ListItem
                key={machine.id}
                link="#"
                noChevron
                onClick={pick(() => { haptic("selection"); setShown(machine.id); })}
                title={machine.name}
                footer={linkedSub(machine)}
              >
                <span slot="media"><Link2 size={22} aria-hidden="true" /></span>
                <span slot="after">{check(machine.id === shown)}</span>
              </ListItem>
            ))}
            {here ? (
              <ListItem
                link="#"
                noChevron
                onClick={pick(() => callMobile("addComputer"))}
                title={text("Pair another computer", "配对另一台电脑")}
              >
                <span slot="media"><Plus size={22} aria-hidden="true" /></span>
              </ListItem>
            ) : null}
            {here ? (
              <ListItem
                link="#"
                noChevron
                onClick={pick(() => callMobile("computers"))}
                title={text("Manage computers", "管理电脑")}
              >
                <span slot="media"><Settings2 size={22} aria-hidden="true" /></span>
              </ListItem>
            ) : null}
          </List>
        </ButtonMorph> : null}
      </PhonePortal>
    </>
  );
}

const MENU_WIDTH = 260;

/**
 * A bar button that grows into its menu, as iOS 26's do: one piece of the
 * bar's glass, which keeps its top-left corner and its curve (half the
 * button's height) and widens and lengthens into the menu, the button's
 * mark fading out as the rows fade in; closing shrinks it back into the
 * button. Framework7 has no such menu (its popover is a second shape
 * beside the button), so it is drawn here with Framework7's glass.
 */
function ButtonMorph({ open, from, mark, onClose, children }: {
  open: boolean;
  from: HTMLElement;
  mark: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  // shown: on screen at all; grown: at the menu's size.
  const [shown, setShown] = useState(false);
  const [grown, setGrown] = useState(false);
  const [box, setBox] = useState<{ top: number; left: number; width: number; height: number; color: string } | null>(null);
  const [height, setHeight] = useState(0);
  const body = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (!open) {
      setGrown(false);
      return;
    }
    // The button at rest: a tap scales Framework7's bar button up, and a
    // menu measured then would shrink back to a bigger circle than the
    // button and jump at the end. The layout size, centred on where it is.
    const seen = from.getBoundingClientRect();
    const width = from.offsetWidth;
    const height = from.offsetHeight;
    const link = from.querySelector<HTMLElement>(".link") ?? from;
    setBox({
      top: seen.top + (seen.height - height) / 2,
      left: seen.left + (seen.width - width) / 2,
      width,
      height,
      color: getComputedStyle(link).color,
    });
    setShown(true);
    haptic("light");
    // One frame at the button's size, then the menu's: the change is what
    // the transition plays.
    const frame = requestAnimationFrame(() => requestAnimationFrame(() => setGrown(true)));
    return () => cancelAnimationFrame(frame);
  }, [open, from]);

  useLayoutEffect(() => {
    if (shown && body.current) setHeight(body.current.offsetHeight);
  }, [shown, children]);

  // The bar's own glass hides while this piece of it is out.
  useEffect(() => {
    if (!shown) return;
    from.style.visibility = "hidden";
    return () => { from.style.visibility = ""; };
  }, [shown, from]);

  if (!shown || !box) return null;
  const radius = box.height / 2;
  return (
    <>
      <div className="phone-morph-backdrop" onClick={onClose} />
      <div
        className="phone-morph"
        data-grown={grown ? "" : undefined}
        style={{
          top: box.top,
          left: box.left,
          width: grown ? Math.max(MENU_WIDTH, box.width) : box.width,
          height: grown ? Math.max(height, box.height) : box.height,
          borderRadius: radius,
        }}
        onTransitionEnd={(event) => {
          if (event.target === event.currentTarget && event.propertyName === "height" && !grown) setShown(false);
        }}
      >
        <div className="phone-morph-mark" style={{ width: box.width, height: box.height, color: box.color }} aria-hidden="true">{mark}</div>
        <div ref={body} className="phone-morph-body" style={{ width: Math.max(MENU_WIDTH, box.width) }}>{children}</div>
      </div>
    </>
  );
}
