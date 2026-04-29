/**
 * Toast — transient notification bubble.
 *
 * Use for: "Saved", "Sent to wechat", "Disconnected", "Reconnected"
 * — short messages that should appear briefly and disappear without
 * the user having to dismiss them.
 *
 * Different from <Alert> which is inline + persistent. Different
 * from <Modal> which is interactive + blocks input.
 *
 *     const toast = useToast();
 *     toast.show('Bound!', { variant: 'success', durationMs: 3000 });
 *
 * Rendered by <ToastHost>, mounted once at the root next to
 * <ModalHost>. Multiple toasts stack vertically (newest at top).
 * Each fades after ``durationMs`` (default 4s) — actually just
 * disappears since terminals don't fade; but the API stays
 * future-compatible.
 */
import React, {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Box, Text } from '../runtime/index';
import { useColors } from '../theme/ThemeProvider.js';
import type { AlertVariant } from './Alert.js';

export interface ToastEntry {
  id: number;
  message: string;
  variant: AlertVariant;
  durationMs: number;
  createdAt: number;
}

export interface ToastApi {
  show: (message: string, opts?: { variant?: AlertVariant; durationMs?: number }) => void;
  dismiss: (id: number) => void;
  clear: () => void;
  toasts: readonly ToastEntry[];
}

const ToastContext = createContext<ToastApi | null>(null);

export interface ToastProviderProps {
  children: ReactNode;
  /** Cap on simultaneous toasts. Older drop off. Default 5. */
  maxConcurrent?: number;
}

let _toastId = 0;

export const ToastProvider: React.FC<ToastProviderProps> = ({
  children, maxConcurrent = 5,
}) => {
  const [toasts, setToasts] = useState<ToastEntry[]>([]);
  const timersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: number) => {
    setToasts((s) => s.filter((t) => t.id !== id));
    const timer = timersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
  }, []);

  const show: ToastApi['show'] = useCallback((message, opts) => {
    const id = ++_toastId;
    const variant = opts?.variant ?? 'info';
    const durationMs = opts?.durationMs ?? 4000;
    setToasts((s) => {
      const next = [...s, {
        id, message, variant, durationMs, createdAt: Date.now(),
      }];
      // Drop oldest if over cap.
      if (next.length > maxConcurrent) return next.slice(next.length - maxConcurrent);
      return next;
    });
    if (durationMs > 0) {
      const timer = setTimeout(() => dismiss(id), durationMs);
      timersRef.current.set(id, timer);
    }
  }, [dismiss, maxConcurrent]);

  const clear: ToastApi['clear'] = useCallback(() => {
    setToasts([]);
    for (const t of timersRef.current.values()) clearTimeout(t);
    timersRef.current.clear();
  }, []);

  // Cleanup timers on unmount.
  useEffect(() => () => {
    for (const t of timersRef.current.values()) clearTimeout(t);
    timersRef.current.clear();
  }, []);

  const api = useMemo<ToastApi>(() => ({
    show, dismiss, clear, toasts,
  }), [show, dismiss, clear, toasts]);

  return <ToastContext.Provider value={api}>{children}</ToastContext.Provider>;
};

export function useToast(): ToastApi {
  const v = useContext(ToastContext);
  if (!v) throw new Error('useToast must be called inside <ToastProvider>');
  return v;
}

/**
 * Render slot for active toasts. Mount once near the root, after
 * <ModalHost> so toasts overlay any open modal. Renders nothing
 * when no toasts active.
 */
export const ToastHost: React.FC = () => {
  const { toasts } = useToast();
  const colors = useColors();
  if (toasts.length === 0) return null;
  return (
    <Box flexDirection="column" alignItems="flex-end" paddingRight={1}>
      {toasts.map((t) => {
        const color =
          t.variant === 'error'   ? colors.error :
          t.variant === 'warning' ? colors.warning :
          t.variant === 'success' ? colors.success :
                                    colors.primary;
        return (
          <Box
            key={t.id}
            borderStyle="single"
            borderColor={color}
            paddingX={1}
            marginTop={0}
          >
            <Text color={color}>{t.message}</Text>
          </Box>
        );
      })}
    </Box>
  );
};
