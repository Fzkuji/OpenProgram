/**
 * <Select> — single-choice picker with built-in type-to-filter.
 *
 * This is the kit's standard list picker. Use it inline (as part of
 * a screen) or push it onto the modal stack. ``onSelect`` fires with
 * the chosen item's ``value``; ``onCancel`` fires on esc.
 *
 * For multi-step flows (Form), prefer <Form> which composes Select
 * + Input + Confirm and tracks step state. Standalone Select is for
 * one-off choices (theme picker, model switcher, etc.).
 *
 * Implementation: thin wrapper over the existing Picker component
 * (which has been battle-tested in REPL). The point of the kit
 * version is a stable, kit-namespaced import so screens can move to
 * ``import { Select } from '../ui'`` and we can swap the
 * implementation later without touching call sites.
 */
import React from 'react';
import { Picker, type PickerItem } from '../components/Picker.js';

export interface SelectOption<V = string> {
  /** Visible label. */
  label: string;
  /** Optional description (gray, right-aligned). */
  description?: string;
  /** Value passed back to onSelect. Use ``string`` unless you need
   *  richer values; sticking to strings keeps the back-pointer stable
   *  across re-renders (referential equality matters for the Picker's
   *  initial-index calc when we add it). */
  value: V;
  /** Disable the row (rendered dim, can't be selected). Currently a
   *  no-op — placeholder so call sites can opt into the API early. */
  disabled?: boolean;
}

export interface SelectProps<V = string> {
  title: string;
  options: SelectOption<V>[];
  onSelect: (value: V, option: SelectOption<V>) => void;
  onCancel?: () => void;
  /** Cap on visible rows. Default 12. Matches Picker. */
  maxVisible?: number;
}

export function Select<V = string>({
  title, options, onSelect, onCancel, maxVisible,
}: SelectProps<V>): React.ReactElement {
  const items: PickerItem<SelectOption<V>>[] = options.map((o) => ({
    label: o.label,
    description: o.description,
    value: o,
  }));
  return (
    <Picker
      title={title}
      items={items}
      onSelect={(it) => onSelect(it.value.value, it.value)}
      onCancel={() => onCancel?.()}
      maxVisible={maxVisible}
    />
  );
}
