/**
 * Modal stack manager.
 *
 * Why a stack: the channel-binding flow walks several "modal" steps
 * — pick channel, pick account, scan QR, choose binding mode, enter
 * peer id. Each step needs its own focus + its own esc-to-cancel
 * semantics. Hand-rolling that as a single ``pickerKind`` enum has
 * led to bugs (esc closes everything, focus leaks). A stack matches
 * the flow naturally: push to advance, pop to back-up, esc pops the
 * top.
 *
 * API:
 *
 *     const modal = useModal();
 *     modal.push(<Picker ... />, { onClose: () => ..., title: 'Foo' });
 *     // user presses esc → onClose fires, modal pops automatically
 *     modal.replace(<NextPicker />);   // navigate forward, no stack growth
 *     modal.pop();                      // close current
 *     modal.clear();                    // close all (used by /clear)
 *
 *     // Inside a modal child:
 *     const ctx = useCurrentModal();    // { close, replace } for this layer
 *
 * Rendering: <ModalHost> sits inside <Shell>, replaces the prompt /
 * picker slot when the stack is non-empty. Only the top-of-stack
 * renders — non-top entries are kept in state for "back" but not
 * drawn. Same UX as native iOS push navigation.
 *
 * z-order: a single render slot at any time, no overlap. If we ever
 * need true overlay (e.g. transient toast over a modal), Toast does
 * that as a separate concern.
 */
import React, {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from 'react';

export interface ModalEntry {
  id: number;
  content: ReactNode;
  onClose?: () => void;
  /** Optional label — currently unused but kept so a future status
   *  bar can show breadcrumb of nested modals. */
  title?: string;
}

export interface ModalApi {
  push: (content: ReactNode, opts?: { onClose?: () => void; title?: string }) => void;
  /** Replace the top-of-stack entry (forward nav, no back-stack growth). */
  replace: (content: ReactNode, opts?: { onClose?: () => void; title?: string }) => void;
  /** Close the top entry. Calls its onClose handler before removing. */
  pop: () => void;
  /** Close the entire stack. */
  clear: () => void;
  /** Read-only view of the stack — components rarely need this; <ModalHost>
   *  uses it to render. */
  stack: readonly ModalEntry[];
}

const ModalContext = createContext<ModalApi | null>(null);

export interface ModalProviderProps {
  children: ReactNode;
}

let _id = 0;

export const ModalProvider: React.FC<ModalProviderProps> = ({ children }) => {
  const [stack, setStack] = useState<ModalEntry[]>([]);

  const push: ModalApi['push'] = useCallback((content, opts) => {
    setStack((s) => [...s, { id: ++_id, content, ...opts }]);
  }, []);

  const replace: ModalApi['replace'] = useCallback((content, opts) => {
    setStack((s) => {
      if (s.length === 0) return [{ id: ++_id, content, ...opts }];
      const next = s.slice(0, -1);
      next.push({ id: ++_id, content, ...opts });
      return next;
    });
  }, []);

  const pop: ModalApi['pop'] = useCallback(() => {
    setStack((s) => {
      if (s.length === 0) return s;
      const top = s[s.length - 1]!;
      try {
        top.onClose?.();
      } catch {
        // onClose handlers are best-effort — a thrown handler
        // shouldn't strand the stack with a half-popped entry.
      }
      return s.slice(0, -1);
    });
  }, []);

  const clear: ModalApi['clear'] = useCallback(() => {
    setStack((s) => {
      for (const e of s) {
        try { e.onClose?.(); } catch { /* swallow */ }
      }
      return [];
    });
  }, []);

  const api = useMemo<ModalApi>(() => ({
    push, replace, pop, clear,
    stack,
  }), [push, replace, pop, clear, stack]);

  return <ModalContext.Provider value={api}>{children}</ModalContext.Provider>;
};

export function useModal(): ModalApi {
  const v = useContext(ModalContext);
  if (!v) throw new Error('useModal must be called inside <ModalProvider>');
  return v;
}

/**
 * <ModalHost> — render slot for the current top-of-stack modal.
 * Place it where the modal should appear (typically replacing the
 * PromptInput row inside <Shell>). Returns null when stack is empty.
 *
 * The modal's content is rendered as-is; no chrome, no border. Each
 * modal is responsible for its own framing — different pickers want
 * different visuals.
 */
export const ModalHost: React.FC = () => {
  const { stack } = useModal();
  const top = stack[stack.length - 1];
  if (!top) return null;
  return <React.Fragment key={top.id}>{top.content}</React.Fragment>;
};

/**
 * Inside a modal child, get a per-layer close/replace handle.
 * Useful when the modal wants to navigate forward without writing
 * ``modal.replace(...)`` from each picker — they call
 * ``ctx.replace(<NextThing/>)``. Falls back to no-ops outside a
 * modal so components can be reused as inline widgets too.
 */
export interface CurrentModalApi {
  close: () => void;
  replace: (content: ReactNode, opts?: { onClose?: () => void; title?: string }) => void;
}

export function useCurrentModal(): CurrentModalApi {
  const api = useContext(ModalContext);
  if (!api) {
    return { close: () => {}, replace: () => {} };
  }
  return {
    close: api.pop,
    replace: api.replace,
  };
}
