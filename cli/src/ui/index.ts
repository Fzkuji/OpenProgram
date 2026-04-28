/**
 * OpenProgram TUI Kit — opinionated component library on top of
 * vendored ink.
 *
 * Layers:
 *   Layer 0  vendored ink (DOM): Box, Text, ScrollBox, AlternateScreen,
 *            useInput, etc. — exported from '@openprogram/ink'.
 *   Layer 1  this kit: app-shell, layout, modal, form, feedback. Use
 *            these in screens; do NOT mix with raw ink primitives in
 *            new code (existing screens migrate gradually).
 *   Layer 2  screens (REPL, demo): consume layer 1.
 *
 * Phase 1 surface: enough to migrate REPL off main-buffer flow and
 * fix the resize bugs. Subsequent phases add Modal, Form, Toast.
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

export {
  useTerminalSize,
  useBreakpoint,
  useResponsive,
} from './hooks.js';
export type { TerminalSize, Breakpoint, ResponsiveValue } from './hooks.js';
