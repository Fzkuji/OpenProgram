import { useCallback, useEffect, useRef, useState } from "react";
import type { HTMLAttributes } from "react";

/** A header or chat row of the project lists, in document order. */
type Item = { key: string; group: string; row: boolean; top: number; bottom: number };
type Drag = {
  id: string; from: string; pointerId: number; element: HTMLElement;
  startX: number; startY: number; x: number; y: number; active: boolean;
  scroller: HTMLElement; scrollTop: number;
  items: Item[]; source: number; height: number;
};
/**
 * Where the carried row would land: the project (its own when it is
 * only reordered there), the item the gap opens before, and the chats
 * right before and after that place in the project.
 */
type Placement = { project: string | null; insertion: number; before: string | null; after: string | null };

/** A chat row picked up and carried onto another project group. */
export interface DraggingChat { id: string; dy: number }

/** Props a chat row spreads: `data-dragging` marks the row being carried. */
export type ChatRowProps = HTMLAttributes<HTMLDivElement> & { "data-dragging"?: true; "data-chat-row": string };

const rowKey = (id: string) => `r:${id}`;
const headerKey = (projectId: string) => `h:${projectId}`;
const rowId = (key: string) => key.slice(2);

/**
 * Pointer-capture drag of a chat row onto a project group. The same
 * pattern as `useProjectDrag`: a press becomes a drag after 6px, the
 * row itself slides with the pointer (no separate label), release
 * over a place calls `onDrop` with the project and the chats either
 * side of the place, and Escape, blur or a release elsewhere cancels.
 * A click after a drag is swallowed so the chat does not open.
 *
 * The lists make room for the carried row: headers and rows are one
 * sequence, the row's own centre picks the place in it nearest to
 * where the row is (before the first item still more than half below
 * it, as if the row had left its old place), and the items between
 * its old place and that one shift by one row, closing the gap it left
 * and opening one where it would land (`rowOffset` / `headerOffset`).
 * A place before a header is the end of the project above it. Another
 * project is lit as the drop target (`dropProject`); inside the row's
 * own project the gap alone shows where it goes, and only while
 * `reorder` is on (the lists sort by time, and a place is a time).
 */
/** The resting offsets and growth. `finish` runs when the list mounts with
 *  dragging off; setting these same objects back changes nothing, where new
 *  empty ones would render the list again as the sidebar opens. */
const NO_OFFSETS: Record<string, number> = {};
const NO_GROW = { top: 0, bottom: 0 };

export function useChatDrag(
  enabled: boolean,
  reorder: boolean,
  onDrop: (sessionId: string, projectId: string, beforeId: string | null, afterId: string | null) => void,
) {
  const current = useRef<Drag | null>(null);
  const suppressClick = useRef<string | null>(null);
  const frame = useRef<number | null>(null);
  const [draggingChat, setDraggingChat] = useState<DraggingChat | null>(null);
  const [dropProject, setDropProject] = useState<string | null>(null);
  const [offsets, setOffsets] = useState<Record<string, number>>(NO_OFFSETS);
  // How far (px) the target project's frame reaches past its box, to
  // take in the gap its rows opened: below it when the chat comes from
  // further down, above it when the rows moved up to meet the chat.
  const [dropGrow, setDropGrow] = useState(NO_GROW);

  // Pointer y in the lists' coordinates at the press (their own scroll
  // since then is added back), where the item bounds were measured.
  const listY = (drag: Drag) => drag.y + drag.scroller.scrollTop - drag.scrollTop;

  function placementAt(drag: Drag): Placement {
    const nowhere = { project: null, insertion: drag.source, before: null, after: null };
    const rect = drag.scroller.getBoundingClientRect();
    if (drag.x < rect.left || drag.x > rect.right || drag.y < rect.top || drag.y > rect.bottom) return nowhere;
    const { items, source, height } = drag;
    const centre = (items[source].top + items[source].bottom) / 2 + listY(drag) - drag.startY;
    // Items after the old place sit one row higher once the row has left it.
    let insertion = items.length;
    for (let index = 0; index < items.length; index++) {
      if (index === source) continue;
      if (items[index].bottom - (index > source ? height : 0) > centre) { insertion = index; break; }
    }
    if (insertion === 0) insertion = 1;
    const place = insertion >= items.length ? items[items.length - 1]
      : items[insertion].row ? items[insertion] : items[insertion - 1];
    const project = place.group;
    if (project === drag.from && (!reorder || insertion === source || insertion === source + 1)) return nowhere;
    let before: string | null = null;
    for (let index = insertion - 1; index >= 0 && items[index].group === project; index--) {
      if (index !== source && items[index].row) { before = rowId(items[index].key); break; }
    }
    const next = items[insertion];
    const after = next && next.row && next.group === project ? rowId(next.key) : null;
    return { project, insertion, before, after };
  }
  function offsetsFor(drag: Drag, insertion: number): Record<string, number> {
    const next: Record<string, number> = {};
    // Items between the row's old place and the gap move by one row.
    if (insertion > drag.source) {
      for (let index = drag.source + 1; index < insertion; index++) next[drag.items[index].key] = -drag.height;
    } else {
      for (let index = insertion; index < drag.source; index++) next[drag.items[index].key] = drag.height;
    }
    return next;
  }
  function update(drag: Drag) {
    // The row follows the pointer vertically, in its own scroller: the
    // scroll the drag itself caused moves it along with the list.
    setDraggingChat({ id: drag.id, dy: drag.y - drag.startY + drag.scroller.scrollTop - drag.scrollTop });
    const { project, insertion } = placementAt(drag);
    const next = project !== drag.from ? project : null;
    setDropProject(prev => (prev === next ? prev : next));
    const shifted = offsetsFor(drag, insertion);
    const grow = !next ? NO_GROW : insertion > drag.source ? { top: drag.height, bottom: 0 } : { top: 0, bottom: drag.height };
    setDropGrow(prev => (prev.top === grow.top && prev.bottom === grow.bottom ? prev : grow));
    setOffsets(prev => {
      const keys = Object.keys(shifted);
      if (keys.length === Object.keys(prev).length && keys.every(key => prev[key] === shifted[key])) return prev;
      return shifted;
    });
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
    setDraggingChat(null);
    setDropProject(null);
    setOffsets(NO_OFFSETS);
    setDropGrow(NO_GROW);
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
    const rect = drag.scroller.getBoundingClientRect();
    if (drag.x >= rect.left && drag.x <= rect.right) {
      const delta = drag.y < rect.top + 28 ? -8 : drag.y > rect.bottom - 28 ? 8 : 0;
      if (delta) {
        const previous = drag.scroller.scrollTop;
        drag.scroller.scrollTop += delta;
        if (drag.scroller.scrollTop !== previous) update(drag);
      }
    }
    frame.current = window.requestAnimationFrame(autoScroll);
  }

  /** How far (px) a chat row has moved aside for the carried one. */
  const rowOffset = (id: string) => offsets[rowKey(id)] || 0;
  /** How far (px) a project header has moved aside for the carried chat. */
  const headerOffset = (projectId: string) => offsets[headerKey(projectId)] || 0;

  /** Props for a chat row: `from` is the project group it sits in. */
  function rowProps(id: string, from: string): ChatRowProps {
    const carried = draggingChat?.id === id;
    const shift = rowOffset(id);
    return {
      style: carried
        ? { touchAction: "none", transform: `translateY(${draggingChat.dy}px)`, zIndex: 5 }
        : shift ? { touchAction: "none", transform: `translateY(${shift}px)` } : { touchAction: "none" },
      "data-dragging": carried || undefined,
      "data-chat-row": id,
      onDragStart: event => event.preventDefault(),
      onPointerDown: event => {
        if (!enabled || current.current || !event.isPrimary || event.button !== 0
          || (event.target as Element).closest("button, input")) return;
        suppressClick.current = null;
        const element = event.currentTarget;
        const sidebar = element.closest<HTMLElement>("#sidebar");
        const scroller = element.closest<HTMLElement>(".overflow-y-auto");
        if (!sidebar || !scroller) return;
        const items: Item[] = [];
        for (const group of Array.from(sidebar.querySelectorAll<HTMLElement>("[data-project-id]"))) {
          const projectId = group.dataset.projectId!;
          for (const node of Array.from(group.querySelectorAll<HTMLElement>("[data-project-header], [data-chat-row]"))) {
            const rect = node.getBoundingClientRect();
            const row = node.dataset.chatRow !== undefined;
            items.push({ key: row ? rowKey(node.dataset.chatRow!) : headerKey(projectId), group: projectId, row,
              top: rect.top, bottom: rect.top + rect.height });
          }
        }
        const source = items.findIndex(item => item.key === rowKey(id));
        if (source < 0) return;
        const after = items[source + 1];
        const height = after && after.group === from ? after.top - items[source].top : items[source].bottom - items[source].top + 1;
        current.current = { id, from, element, pointerId: event.pointerId, scroller, scrollTop: scroller.scrollTop,
          items, source, height, startX: event.clientX, startY: event.clientY, x: event.clientX, y: event.clientY, active: false };
        element.setPointerCapture(event.pointerId);
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
        update(drag);
      },
      onPointerUp: event => {
        const drag = current.current;
        if (!drag || drag.pointerId !== event.pointerId) return;
        drag.x = event.clientX; drag.y = event.clientY;
        const place = drag.active ? placementAt(drag) : null;
        finish();
        if (place?.project) onDrop(drag.id, place.project, place.before, place.after);
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
  return { draggingChat, dropProject, dropGrow, rowProps, rowOffset, headerOffset };
}
