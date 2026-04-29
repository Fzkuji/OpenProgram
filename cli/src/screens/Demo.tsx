/**
 * Demo screen — showcases every kit component.
 *
 * Activated by ``openprogram --demo`` (handled in cli/src/index.tsx).
 * No backend / WS connection; pure UI exercise. Useful for:
 *
 *   - visual regression smoke checks (after refactoring layout
 *     primitives, eyeball every component to catch broken rendering)
 *   - showing new contributors what's available in the kit
 *   - verifying resize behavior — drag the terminal narrow / tall
 *     while the demo is open and watch components reflow
 *
 * Each section has a short label so the screen reads like a catalogue.
 */
import React, { useState } from 'react';
import { Box, Text, useApp, useInput } from '../runtime/index';
import {
  Shell, ScrollView,
  ModalHost, useModal,
  ToastHost, useToast,
  Select, Input, Confirm, MultiSelect, Form,
  Stack, Row, Card, Panel, Alert,
  useTerminalSize, useBreakpoint,
} from '../ui/index.js';
import { useColors } from '../theme/ThemeProvider.js';

const Header: React.FC<{ children: string }> = ({ children }) => {
  const colors = useColors();
  return <Text bold color={colors.primary}>{children}</Text>;
};

const Section: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <Stack gap={0}>
    <Header>{title}</Header>
    {children}
  </Stack>
);

const DemoBody: React.FC = () => {
  const colors = useColors();
  const size = useTerminalSize();
  const bp = useBreakpoint();
  const modal = useModal();
  const toast = useToast();
  const [counter, setCounter] = useState(0);

  return (
    <Stack gap={1} paddingX={1}>
      <Header>OpenProgram TUI Kit — demo</Header>
      <Text color={colors.muted}>
        terminal: {size.columns}×{size.rows} · breakpoint: {bp} · counter: {counter}
      </Text>
      <Text color={colors.muted}>
        keys: 1=select 2=input 3=confirm 4=multi 5=form
        t=toast a=alert q=quit
      </Text>

      <Section title="Layout — Stack / Row">
        <Row gap={2}>
          <Card title="Card A"><Text>One</Text></Card>
          <Card title="Card B"><Text>Two</Text></Card>
          <Card title="Card C"><Text>Three</Text></Card>
        </Row>
      </Section>

      <Section title="Surfaces — Panel">
        <Panel title="Panel example">
          <Text>Heavier border, primary color, used for primary dialogs.</Text>
        </Panel>
      </Section>

      <Section title="Alerts (variant)">
        <Alert variant="info" title="info">Routine notice.</Alert>
        <Alert variant="success" title="success">Saved.</Alert>
        <Alert variant="warning" title="warning">Maybe rate-limited.</Alert>
        <Alert variant="error" title="error">QR fetch failed.</Alert>
      </Section>

      <Section title="Toast">
        <Text color={colors.muted}>press T to fire a sample toast.</Text>
      </Section>

      <Section title="Pickers — press 1-5 to open">
        <Text color={colors.muted}>1=select  2=input  3=confirm  4=multiselect  5=form</Text>
      </Section>

      <DemoKeys
        onAction={(action) => {
          switch (action) {
            case 'select': openSelectDemo(modal); break;
            case 'input': openInputDemo(modal, toast); break;
            case 'confirm': openConfirmDemo(modal, toast); break;
            case 'multi': openMultiDemo(modal, toast); break;
            case 'form': openFormDemo(modal, toast); break;
            case 'toast':
              toast.show('Hello! That was T.', { variant: 'success' });
              setCounter((n) => n + 1);
              break;
            case 'alert':
              toast.show('Warning toast', { variant: 'warning' });
              break;
          }
        }}
      />
    </Stack>
  );
};

// ─── Demo "scripts" — each opens a different kit modal flow ───────

function openSelectDemo(modal: ReturnType<typeof useModal>) {
  modal.push(
    <Select
      title="Pick a fruit"
      options={[
        { label: 'Apple',      description: 'crunchy', value: 'apple' },
        { label: 'Banana',     description: 'soft',    value: 'banana' },
        { label: 'Cherry',     description: 'sweet',   value: 'cherry' },
        { label: 'Durian',     description: 'pungent', value: 'durian' },
        { label: 'Elderberry', description: 'tart',    value: 'elderberry' },
      ]}
      onSelect={(v) => modal.pop()}
      onCancel={() => modal.pop()}
    />,
  );
}

function openInputDemo(modal: ReturnType<typeof useModal>, toast: ReturnType<typeof useToast>) {
  modal.push(
    <Input
      label="Type something"
      hint="Anything goes — Enter submits."
      onSubmit={(v) => { toast.show(`You typed: ${v}`); modal.pop(); }}
      onCancel={() => modal.pop()}
    />,
  );
}

function openConfirmDemo(modal: ReturnType<typeof useModal>, toast: ReturnType<typeof useToast>) {
  modal.push(
    <Confirm
      title="Save the world?"
      defaultYes
      onConfirm={() => { toast.show('Saved!', { variant: 'success' }); modal.pop(); }}
      onCancel={() => { toast.show('Maybe next time'); modal.pop(); }}
    />,
  );
}

function openMultiDemo(modal: ReturnType<typeof useModal>, toast: ReturnType<typeof useToast>) {
  modal.push(
    <MultiSelect
      title="Pick toppings"
      options={[
        { label: 'Cheese',    value: 'cheese',  initiallyChecked: true },
        { label: 'Pepperoni', value: 'pepperoni' },
        { label: 'Mushrooms', value: 'mushrooms' },
        { label: 'Olives',    value: 'olives' },
        { label: 'Pineapple', value: 'pineapple', description: 'controversial' },
        { label: 'Anchovy',   value: 'anchovy' },
      ]}
      onSubmit={(picks) => {
        toast.show(`Picked: ${picks.join(', ') || '(none)'}`, { variant: 'info' });
        modal.pop();
      }}
      onCancel={() => modal.pop()}
    />,
  );
}

interface FormData extends Record<string, unknown> {
  size?: 'small' | 'medium' | 'large';
  topping?: string;
  confirm?: boolean;
}

function openFormDemo(modal: ReturnType<typeof useModal>, toast: ReturnType<typeof useToast>) {
  modal.push(
    <Form<FormData>
      steps={[
        {
          id: 'size',
          render: (ctx) => (
            <Select<'small' | 'medium' | 'large'>
              title="Size?"
              options={[
                { label: 'Small',  value: 'small' },
                { label: 'Medium', value: 'medium' },
                { label: 'Large',  value: 'large' },
              ]}
              onSelect={(v) => ctx.next({ size: v })}
              onCancel={ctx.cancel}
            />
          ),
        },
        {
          id: 'topping',
          render: (ctx) => (
            <Input
              label="Topping?"
              hint="Type any single topping"
              onSubmit={(t) => ctx.next({ topping: t })}
              onCancel={ctx.cancel}
            />
          ),
        },
        {
          id: 'confirm',
          render: (ctx) => (
            <Confirm
              title={`Order: ${ctx.data.size} ${ctx.data.topping}?`}
              onConfirm={() => ctx.next({ confirm: true })}
              onCancel={ctx.cancel}
            />
          ),
        },
      ]}
      onComplete={(data) => {
        toast.show(`Order placed: ${data.size} ${data.topping}`, { variant: 'success' });
        modal.pop();
      }}
      onCancel={() => {
        toast.show('Order cancelled');
        modal.pop();
      }}
    />,
  );
}

const DemoKeys: React.FC<{ onAction: (a: string) => void }> = ({ onAction }) => {
  const app = useApp();
  useInput((input) => {
    if (input === '1') onAction('select');
    if (input === '2') onAction('input');
    if (input === '3') onAction('confirm');
    if (input === '4') onAction('multi');
    if (input === '5') onAction('form');
    if (input === 't' || input === 'T') onAction('toast');
    if (input === 'a' || input === 'A') onAction('alert');
    if (input === 'q' || input === 'Q') app.exit();
  });
  return null;
};

export const Demo: React.FC = () => (
  <Shell mode="alt">
    <ScrollView>
      <DemoBody />
    </ScrollView>
    <ModalHost />
    <ToastHost />
  </Shell>
);
