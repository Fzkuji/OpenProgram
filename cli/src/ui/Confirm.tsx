/**
 * <Confirm> — yes/no prompt.
 *
 * Renders as a 2-option <Select> (no filter, no description, just
 * "Yes" / "No"). Built on top of the kit's Select so the visual
 * matches every other picker.
 *
 *     <Confirm
 *       title="Bind every wechat:default message to this conversation?"
 *       defaultYes
 *       onConfirm={() => doBind()}
 *       onCancel={() => modal.pop()}
 *     />
 */
import React from 'react';
import { Select } from './Select.js';

export interface ConfirmProps {
  title: string;
  /** Whether "Yes" sits at index 0 (cursor lands here on mount) or
   *  index 1. Most prompts default to Yes; destructive ones should
   *  set ``defaultYes={false}`` so a stray Enter doesn't fire. */
  defaultYes?: boolean;
  onConfirm: () => void;
  /** Called when user picks No or hits esc. */
  onCancel?: () => void;
  /** Override option labels (e.g. 'Bind' / 'Cancel'). */
  yesLabel?: string;
  noLabel?: string;
}

export const Confirm: React.FC<ConfirmProps> = ({
  title, defaultYes = true, onConfirm, onCancel,
  yesLabel = 'Yes', noLabel = 'No',
}) => {
  const options: Array<{ label: string; value: 'yes' | 'no' }> = defaultYes
    ? [{ label: yesLabel, value: 'yes' }, { label: noLabel, value: 'no' }]
    : [{ label: noLabel, value: 'no' }, { label: yesLabel, value: 'yes' }];
  return (
    <Select<'yes' | 'no'>
      title={title}
      options={options}
      onSelect={(v) => {
        if (v === 'yes') onConfirm();
        else onCancel?.();
      }}
      onCancel={() => onCancel?.()}
    />
  );
};
