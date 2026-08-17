export { default as useStderr } from './hooks/use-stderr.js'
export type { StderrHandle } from './hooks/use-stderr.js'
export { default as useStdout } from './hooks/use-stdout.js'
export type { StdoutHandle } from './hooks/use-stdout.js'
export { Ansi } from './ink/Ansi.js'
export { AlternateScreen } from './ink/components/AlternateScreen.js'
export { default as Box } from './ink/components/Box.js'
export type { Props as BoxProps } from './ink/components/Box.js'
export { default as Link } from './ink/components/Link.js'
export { default as Newline } from './ink/components/Newline.js'
export { NoSelect } from './ink/components/NoSelect.js'
export { RawAnsi } from './ink/components/RawAnsi.js'
export { default as ScrollBox } from './ink/components/ScrollBox.js'
export type { ScrollBoxHandle, ScrollBoxProps } from './ink/components/ScrollBox.js'
export { default as Spacer } from './ink/components/Spacer.js'
export type { Props as StdinProps } from './ink/components/StdinContext.js'
export { default as Text } from './ink/components/Text.js'
export type { Props as TextProps } from './ink/components/Text.js'
export type { Key } from './ink/events/input-event.js'
export { default as useApp } from './ink/hooks/use-app.js'
export { useDeclaredCursor } from './ink/hooks/use-declared-cursor.js'
export { type RunExternalProcess, useExternalProcess, withInkSuspended } from './ink/hooks/use-external-process.js'
export { default as useInput } from './ink/hooks/use-input.js'
export { useScrollbackWriter } from './ink/hooks/use-scrollback-writer.js'
export { useHasSelection, useSelection } from './ink/hooks/use-selection.js'
export { default as useStdin } from './ink/hooks/use-stdin.js'
export { useTabStatus } from './ink/hooks/use-tab-status.js'
export { useTerminalFocus } from './ink/hooks/use-terminal-focus.js'
export { useTerminalSize } from './ink/hooks/use-terminal-size.js'
export type { TerminalSize } from './ink/hooks/use-terminal-size.js'
export { useTerminalTitle } from './ink/hooks/use-terminal-title.js'
export { useTerminalViewport } from './ink/hooks/use-terminal-viewport.js'
export { default as measureElement } from './ink/measure-element.js'
export { createRoot, default as render, renderSync } from './ink/root.js'
export type { Instance, RenderOptions, Root } from './ink/root.js'
export { stringWidth } from './ink/stringWidth.js'
// TextInput re-export removed — pulls in stock ink@7 as a transitive
// dep, which collides with the cell-grid renderer here (two ink
// instances, two React-reconciler trees). The OpenProgram TUI has its
// own PromptInput component anyway.
export type {
  Color,
  RGBColor,
  HexColor,
  Ansi256Color,
  AnsiColor,
  TextStyles,
} from './ink/styles.js'
