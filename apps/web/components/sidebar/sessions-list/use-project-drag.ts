import { useCallback, useEffect, useRef, useState } from "react";
import type { HTMLAttributes } from "react";

type Drop = { id: string; side: "before" | "after" };
type Drag = {
  id: string; pointerId: number; element: HTMLElement;
  startX: number; startY: number; x: number; y: number; active: boolean;
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
    const group = drag.element.ownerDocument.elementFromPoint(drag.x, drag.y)?.closest<HTMLElement>("[data-project-id]");
    if (!group || group.closest("#sidebar") !== drag.element.closest("#sidebar")) return null;
    const id = group.dataset.projectId!;
    if (id === drag.id) return null;
    const header = group.firstElementChild!.getBoundingClientRect();
    return { id, side: drag.y < header.top + header.height / 2 ? "before" : "after" };
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
      if (delta) { scroller.scrollTop += delta; updateDrop(drag); }
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
        current.current = { id, element: event.currentTarget, pointerId: event.pointerId,
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
  return { draggingProject, projectDrop, headerProps };
}
