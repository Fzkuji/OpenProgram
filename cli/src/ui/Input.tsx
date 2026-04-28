/**
 * <Input> — single-line text input.
 *
 * Standard text-entry primitive. Used for: peer ids, account names,
 * bot tokens (with ``mask``), search queries.
 *
 * Wraps the existing LineInput component for now. Same migration
 * story as Select — call sites import from '../ui' so we can swap
 * the implementation without touching screens.
 */
import React from 'react';
import { LineInput } from '../components/LineInput.js';

export interface InputProps {
  label: string;
  hint?: string;
  /** Replace each typed char with `•`. For tokens / passwords. */
  mask?: boolean;
  initial?: string;
  onSubmit: (value: string) => void;
  onCancel?: () => void;
  /** Optional validator. Called on submit; return a string error
   *  to reject and keep the input open, or undefined to accept.
   *  Currently a no-op (LineInput has no built-in validation),
   *  reserved for future tightening. */
  validate?: (value: string) => string | undefined;
}

export const Input: React.FC<InputProps> = ({
  label, hint, mask, initial, onSubmit, onCancel,
}) => (
  <LineInput
    label={label}
    hint={hint}
    mask={mask}
    initial={initial}
    onSubmit={onSubmit}
    onCancel={() => onCancel?.()}
  />
);
