/**
 * <Form> — multi-step wizard orchestrator.
 *
 * The pattern that keeps showing up: pick channel → pick account →
 * (login if missing) → choose binding → pick peer. Each step is
 * conditional on the previous step's answer, and the user can hit
 * esc to back up. Hand-rolled today as a 11-state pickerKind switch
 * in REPL.
 *
 * <Form> abstracts that:
 *
 *     <Form
 *       steps={[
 *         {
 *           id: 'channel',
 *           title: 'Pick channel',
 *           render: (ctx) => <Select options={[...]} onSelect={(v) => ctx.next({channel: v})} onCancel={ctx.cancel} />,
 *         },
 *         {
 *           id: 'account',
 *           title: 'Pick account',
 *           skipWhen: (data) => !data.channel,
 *           render: (ctx) => <Select ... onSelect={(v) => ctx.next({account: v})} />,
 *         },
 *         {
 *           id: 'login',
 *           skipWhen: (data) => isAlreadyConfigured(data.channel, data.account),
 *           render: (ctx) => <QrLoginPanel onDone={ctx.next} />,
 *         },
 *       ]}
 *       onComplete={(data) => attachSession(data)}
 *       onCancel={() => modal.pop()}
 *     />
 *
 * Each step's ``render`` receives a ``ctx`` with:
 *   - ``data``  — accumulated answers from all prior steps
 *   - ``next``  — advance to next step, optionally merging more data
 *   - ``back``  — go to previous step (no-op if first)
 *   - ``cancel``— exit the form (calls Form's onCancel)
 *
 * If a step's ``skipWhen(data)`` returns true, the form auto-advances
 * past it. Useful for "if QR creds already exist, skip the QR step".
 *
 * Internal state: just a step index + a data accumulator. No fancy
 * router. Steps render one at a time — only the current step is
 * mounted, so heavy children (e.g. live QR poller) clean up when
 * the user backs up.
 */
import React, { useEffect, useState, type ReactNode } from 'react';

export interface FormStepContext<D> {
  data: D;
  /** Advance to next step. Optionally merge more data. */
  next: (extra?: Partial<D>) => void;
  /** Go to previous step (no-op if at first). */
  back: () => void;
  /** Exit the form via onCancel. */
  cancel: () => void;
}

export interface FormStep<D> {
  /** Stable identifier — used as the React key for the step's
   *  rendered subtree, so navigating away/back unmounts cleanly. */
  id: string;
  /** Optional human title — currently unused; reserved for a future
   *  breadcrumb. */
  title?: string;
  /** Skip this step when ``skipWhen(data)`` returns true. */
  skipWhen?: (data: D) => boolean;
  render: (ctx: FormStepContext<D>) => ReactNode;
}

export interface FormProps<D> {
  steps: FormStep<D>[];
  initialData?: D;
  onComplete: (data: D) => void;
  onCancel?: () => void;
}

export function Form<D extends Record<string, unknown>>({
  steps, initialData, onComplete, onCancel,
}: FormProps<D>): React.ReactElement | null {
  const [stepIdx, setStepIdx] = useState(0);
  const [data, setData] = useState<D>(initialData ?? ({} as D));

  // Auto-skip steps whose skipWhen() returns true. We do this in
  // useEffect (not render) so React commits the previous step's
  // unmount cleanly before we move forward — otherwise a step's
  // cleanup (e.g. cancel a poller) races with the next mount.
  useEffect(() => {
    if (stepIdx >= steps.length) return;
    const cur = steps[stepIdx]!;
    if (cur.skipWhen?.(data)) {
      setStepIdx((i) => i + 1);
    }
  }, [stepIdx, data, steps]);

  // All steps consumed → form complete.
  useEffect(() => {
    if (stepIdx >= steps.length) {
      onComplete(data);
    }
  }, [stepIdx, steps.length, data, onComplete]);

  if (stepIdx >= steps.length) return null;
  const step = steps[stepIdx]!;
  if (step.skipWhen?.(data)) return null;

  const ctx: FormStepContext<D> = {
    data,
    next: (extra) => {
      if (extra) setData((d) => ({ ...d, ...extra }));
      setStepIdx((i) => i + 1);
    },
    back: () => {
      setStepIdx((i) => Math.max(0, i - 1));
    },
    cancel: () => {
      onCancel?.();
    },
  };

  return <React.Fragment key={step.id}>{step.render(ctx)}</React.Fragment>;
}
