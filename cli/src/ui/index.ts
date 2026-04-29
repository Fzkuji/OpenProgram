/**
 * OpenProgram TUI Kit — opinionated component library on top of
 * the local runtime.
 *
 * Layers:
 *   Layer 0  runtime DOM: Box, Text, ScrollBox, AlternateScreen,
 *            useInput, etc. — exported from '../runtime/index'.
 *   Layer 1  this kit: app-shell, layout, modal, form, feedback. Use
 *            these in screens; do NOT mix with raw ink primitives in
 *            new code (existing screens migrate gradually).
 *   Layer 2  screens (REPL, demo): consume layer 1.
 *
 * Shell + ScrollView provide the persistent full-screen chat layout.
 * Modal, Form, Toast, and layout helpers cover the remaining screen UI.
 */

export { Shell } from './Shell.js';
export type { ShellProps } from './Shell.js';

export { ScrollView } from './ScrollView.js';
export type { ScrollViewProps } from './ScrollView.js';

export {
  ModalProvider,
  ModalHost,
  useModal,
  useCurrentModal,
} from './ModalProvider.js';
export type {
  ModalApi,
  ModalEntry,
  ModalProviderProps,
  CurrentModalApi,
} from './ModalProvider.js';

export { Select } from './Select.js';
export type { SelectOption, SelectProps } from './Select.js';

export { Input } from './Input.js';
export type { InputProps } from './Input.js';

export { Confirm } from './Confirm.js';
export type { ConfirmProps } from './Confirm.js';

export { MultiSelect } from './MultiSelect.js';
export type { MultiSelectOption, MultiSelectProps } from './MultiSelect.js';

export { Form } from './Form.js';
export type { FormProps, FormStep, FormStepContext } from './Form.js';

export { Stack, Row, Spacer, Center } from './layout.js';
export type { StackRowProps, CenterProps } from './layout.js';

export { Card, Panel } from './Card.js';
export type { CardProps } from './Card.js';

export { Alert } from './Alert.js';
export type { AlertProps, AlertVariant } from './Alert.js';

export {
  ToastProvider,
  ToastHost,
  useToast,
} from './ToastProvider.js';
export type {
  ToastApi,
  ToastEntry,
  ToastProviderProps,
} from './ToastProvider.js';

export {
  useTerminalSize,
  useBreakpoint,
  useResponsive,
} from './hooks.js';
export type { TerminalSize, Breakpoint, ResponsiveValue } from './hooks.js';
