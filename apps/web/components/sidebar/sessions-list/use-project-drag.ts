import { useCallback, useEffect, useRef, useState } from "react";
import type { HTMLAttributes } from "react";

type Drop = { id: string; side: "before" | "after" };
type Slot = { id: string; top: number; height: number; center: number };
type Drag = {
  id: string; pointerId: number; element: HTMLElement;
  startX: number; startY: number; x: number; y: number; active: boolean;
  scroller: HTMLElement; scrollTop: number; slots: Slot[];
};

/** Pointer capture keeps project ordering independent of the OS drag session. */
export function useProjectDrag(
  enabled: boolean,
  onMove: (source: string, target: string, side: Drop["side"]) => void,
) {
  const current = useRef<Drag | null>(null);
  const suppressClick = useRef<string | null>(null);
  const frame = useRef<number | null>(null);
  const [draggingProject, setDraggingProject] = useState<{ id: string; x: number; y: number } | null>(null);
  const [projectDrop, setProjectDrop] = useState<Drop | null>(null);

  function dropAt(drag: Drag): Drop | null {
    const rect = drag.scroller.getBoundingClientRect();
    if (drag.x < rect.left || drag.x > rect.right || drag.y < rect.top || drag.y > rect.bottom) return null;
    // Use the original layout so animated neighbors never change the hit test.
    const y = drag.y + drag.scroller.scrollTop - drag.scrollTop;
    const slot = drag.slots.find((item, index) => y < (drag.slots[index + 1]?.top ?? Infinity));
    if (!slot || slot.id === drag.id) return null;
    return { id: slot.id, side: y < slot.center ? "before" : "after" };
  }
  function updateDrop(drag: Drag) {
    const next = dropAt(drag);
    setProjectDrop(prev => prev?.id === next?.id && prev?.side === next?.side ? prev : next);
  }
  const finish = useCallback(() => {
    const drag = current.current;
    current.current = null;
    if (frame.current !== null) window.cancelAnimationFrame(frame.current);
    frame.current = null;
    if (drag) {
      if (drag.active) suppressClick.current = drag.id;
      if (drag.element.hasPointerCapture(drag.pointerId)) drag.element.releasePointerCapture(drag.pointerId);
    }
    setDraggingProject(null);
    setProjectDrop(null);
  }, []);

  useEffect(() => {
    if (!enabled) { finish(); return; }
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") finish(); };
    window.addEventListener("keydown", escape);
    window.addEventListener("blur", finish);
    return () => {
      window.removeEventListener("keydown", escape);
      window.removeEventListener("blur", finish);
      finish();
    };
  }, [enabled, finish]);

  function autoScroll() {
    const drag = current.current;
    if (!drag?.active) return;
    const scroller = drag.element.closest<HTMLElement>(".overflow-y-auto");
    if (!scroller) return;
    const rect = scroller.getBoundingClientRect();
    if (drag.x >= rect.left && drag.x <= rect.right) {
      const delta = drag.y < rect.top + 28 ? -8 : drag.y > rect.bottom - 28 ? 8 : 0;
      if (delta) {
        const previous = scroller.scrollTop;
        scroller.scrollTop += delta;
        if (scroller.scrollTop !== previous) {
          setDraggingProject({ id: drag.id, x: drag.x, y: drag.y });
          updateDrop(drag);
        }
      }
    }
    frame.current = window.requestAnimationFrame(autoScroll);
  }

  function headerProps(id: string): HTMLAttributes<HTMLDivElement> {
    return {
      style: { touchAction: "none" },
      onDragStart: event => event.preventDefault(),
      onPointerDown: event => {
        if (!enabled || current.current || !event.isPrimary || event.button !== 0 || (event.target as Element).closest("button")) return;
        suppressClick.current = null;
        const element = event.currentTarget;
        const sidebar = element.closest<HTMLElement>("#sidebar");
        const scroller = element.closest<HTMLElement>(".overflow-y-auto");
        if (!sidebar || !scroller) return;
        const slots = Array.from(sidebar.querySelectorAll<HTMLElement>("[data-project-id]")).map(group => {
          const bounds = group.getBoundingClientRect();
          const header = group.querySelector<HTMLElement>("[aria-keyshortcuts]")!.getBoundingClientRect();
          return { id: group.dataset.projectId!, top: bounds.top, height: bounds.height, center: header.top + header.height / 2 };
        });
        current.current = { id, element, pointerId: event.pointerId, scroller, scrollTop: scroller.scrollTop, slots,
          startX: event.clientX, startY: event.clientY, x: event.clientX, y: event.clientY, active: false };
        event.currentTarget.setPointerCapture(event.pointerId);
      },
      onPointerMove: event => {
        const drag = current.current;
        if (!drag || drag.pointerId !== event.pointerId) return;
        drag.x = event.clientX; drag.y = event.clientY;
        if (!drag.active) {
          if (Math.hypot(drag.x - drag.startX, drag.y - drag.startY) < 6) return;
          drag.active = true;
          autoScroll();
        }
        event.preventDefault();
        setDraggingProject({ id, x: drag.x, y: drag.y });
        updateDrop(drag);
      },
      onPointerUp: event => {
        const drag = current.current;
        if (!drag || drag.pointerId !== event.pointerId) return;
        drag.x = event.clientX; drag.y = event.clientY;
        const target = drag.active ? dropAt(drag) : null;
        finish();
        if (target) onMove(drag.id, target.id, target.side);
      },
      onPointerCancel: event => { if (current.current?.pointerId === event.pointerId) finish(); },
      onLostPointerCapture: event => { if (current.current?.pointerId === event.pointerId) finish(); },
      onClick: event => {
        if (suppressClick.current === id && event.detail !== 0) {
          suppressClick.current = null;
          event.preventDefault(); event.stopPropagation();
        }
      },
    };
  }
  function projectOffset(id: string): number {
    const drag = current.current;
    if (!drag?.active) return 0;
    if (id === drag.id) return drag.y - drag.startY + drag.scroller.scrollTop - drag.scrollTop;
    if (!projectDrop) return 0;
    const source = drag.slots.findIndex(slot => slot.id === drag.id);
    const target = drag.slots.findIndex(slot => slot.id === projectDrop.id);
    const index = drag.slots.findIndex(slot => slot.id === id);
    const destination = target + Number(projectDrop.side === "after") - Number(source < target);
    const slot = drag.slots[source];
    const gap = source + 1 < drag.slots.length ? drag.slots[source + 1].top - slot.top - slot.height
      : source > 0 ? slot.top - drag.slots[source - 1].top - drag.slots[source - 1].height : 1;
    const distance = slot.height + Math.max(1, Math.min(8, gap));
    if (source < index && index <= destination) return -distance;
    if (destination <= index && index < source) return distance;
    return 0;
  }
  return { draggingProject, projectDrop, projectOffset, headerProps };
}
