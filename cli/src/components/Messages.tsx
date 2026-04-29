import React from 'react';
import { TurnRow, Turn } from './Turn.js';

export interface MessagesProps {
  /**
   * The full chat transcript. With hermes-ink there's no <Static> —
   * every render is a full cell-grid frame, so committed turns and
   * the streaming turn live in the same React tree and reflow on
   * every resize / theme change.
   */
  committed: Turn[];
  /** Currently-streaming assistant turn, if any. */
  streaming?: Turn | null;
}

export const Messages: React.FC<MessagesProps> = ({
  committed, streaming,
}) => {
  return (
    <>
      {committed.map((turn) => <TurnRow key={turn.id} turn={turn} />)}
      {streaming ? <TurnRow turn={streaming} /> : null}
    </>
  );
};
