# OpenProgram TUI Kit

`cli/src/ui/` — application-side component library for OpenProgram's
terminal UI. It sits on top of the local runtime in `cli/src/runtime/`.

## Why this exists

The local runtime gives us a terminal DOM (Box / Text / ScrollBox /
AlternateScreen / Yoga flex / mouse + keyboard events). What we lacked
was the equivalent of "shadcn/ui" — a stable, opinionated set of
components that screens write against, so the app code reads like
HTML+JSX and behaves consistently across resize, focus, and modal
state.

Without this layer, each screen had separate picker, input, and resize
code. That produced inconsistent escape-key behavior, repeated layout
logic, and resize artifacts.

## Layers

```
Layer 0   cli/src/runtime/        DOM primitives
Layer 1   cli/src/ui/             ← this kit
Layer 2   cli/src/screens/        REPL etc., consumes Layer 1
```

Screens should import runtime primitives from `../runtime/index` only
when they need raw Box/Text/useInput-level APIs. Prefer the kit's
components for shell, scroll, modal, form, feedback, and layout code.

## Quick reference

```tsx
import {
  // App shell — wraps everything
  Shell,

  // Generic scrolling area, sticky-bottom optional
  ScrollView,

  // Modal stack
  ModalProvider, ModalHost, useModal, useCurrentModal,

  // Toast (transient notifications)
  ToastProvider, ToastHost, useToast,

  // Form-ish components
  Select, Input, Confirm, MultiSelect, Form,

  // Layout primitives
  Stack, Row, Spacer, Center,

  // Surfaces
  Card, Panel,

  // Inline feedback
  Alert,

  // Hooks
  useTerminalSize, useBreakpoint, useResponsive,
} from '../ui';
```

## Patterns

### App shell

```tsx
<Shell mode="alt">
  <TranscriptViewport stickyBottom>{transcript}</TranscriptViewport>
  <PromptInput />
  <BottomBar />
</Shell>
```

For persistent full-screen screens, use `<Shell mode="alt">`. It wraps
`<AlternateScreen>`, applies `width=cols, height=rows` on the root, and
provides `ModalProvider` + `ToastProvider`. Children flex down by
default.

REPL should use `cli/src/components/TranscriptViewport.tsx` for the
chat transcript. It owns transcript wheel/PageUp/PageDown/Home/End
handling and the app scrollbar, while `PromptInput` owns only text
editing, completion, and submit behavior. `ScrollView` remains the
generic scroll container for screens that do not have a fixed composer.

### Modal stack (replacing pickerKind switch)

```tsx
const modal = useModal();

const openChannelPicker = () => {
  modal.push(
    <Select
      title="Pick a channel"
      options={[{label:'wechat',value:'wechat'}, ...]}
      onSelect={(channel) => {
        modal.replace(<AccountPicker channel={channel} />);
      }}
      onCancel={() => modal.pop()}
    />,
    { onClose: () => { /* cleanup if needed */ } }
  );
};
```

esc pops the top automatically. `replace` swaps the top entry without
growing the stack (forward navigation). `pop` calls the entry's
`onClose` and removes it.

### Multi-step form

```tsx
type Data = { channel?: string; account?: string; peer?: string };

<Form<Data>
  steps={[
    { id: 'channel', render: (ctx) => (
        <Select options={CHANNELS} onSelect={(channel) => ctx.next({channel})} />
    )},
    { id: 'account', render: (ctx) => (
        <Select options={accountsFor(ctx.data.channel)} onSelect={(account) => ctx.next({account})} />
    )},
    { id: 'login', skipWhen: (d) => isConfigured(d.channel, d.account),
      render: (ctx) => <QrLogin onDone={() => ctx.next()} /> },
    { id: 'confirm', render: (ctx) => (
        <Confirm title={`Bind ${ctx.data.channel}:${ctx.data.account}?`} onConfirm={() => ctx.next()} />
    )},
  ]}
  onComplete={(data) => attachSession(data)}
  onCancel={() => modal.pop()}
/>
```

### Toasts

```tsx
const toast = useToast();
toast.show('Bound!', { variant: 'success' });
toast.show('Connection lost', { variant: 'error', durationMs: 0 }); // 0 = sticky
```

### Layout

```tsx
<Stack gap={1}>
  <Title>Section</Title>
  <Para>Body text wrapping at the container's width.</Para>
  <Row gap={2}>
    <Card title="Left">…</Card>
    <Card title="Right">…</Card>
  </Row>
</Stack>
```

### Responsive

```tsx
const bp = useBreakpoint();        // 'xs' | 'sm' | 'md' | 'lg'
const cols = useResponsive({ xs: 1, sm: 2, md: 4, default: 4 });
```

Default thresholds: xs<60, sm<100, md<140, else lg.

## Compatibility matrix

| Component | Inside Modal | Inline | Standalone |
|---|---|---|---|
| Select | ✓ | ✓ | ✓ |
| Input | ✓ | ✓ | ✓ |
| Confirm | ✓ | ✓ | ✓ |
| MultiSelect | ✓ | ✓ | ✓ |
| Form | ✓ | ✓ | ✓ |
| Toast | host once at root | n/a | n/a |

## Migration from legacy components

| Old | New |
|---|---|
| `cli/src/components/Picker.tsx` | `Select` (kit re-exports it) |
| `cli/src/components/LineInput.tsx` | `Input` (same) |
| `useTerminalWidth/Height/PanelWidth` | `useTerminalSize` |
| `pushSystem` for system messages | `Alert` (inline) or `useToast` (transient) |
| 11-branch `pickerKind` switch | `useModal().push()` per picker |

The legacy components stay around for backward compat — kit components
internally wrap them. Migrate screens at your own pace; `cli/src/ui`
exports stay stable while internals can swap.

## Testing

Run the demo screen to visually inspect every component:

```sh
openprogram --demo
```

(see `cli/src/screens/Demo.tsx`)

Component-level unit tests live alongside each component as
`Component.test.tsx` once we have a TUI test framework wired.
