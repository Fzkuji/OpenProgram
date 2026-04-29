import React from 'react';
import { TurnRow, Turn } from './Turn.js';
import { Welcome, WelcomeStats } from './Welcome.js';

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
  /** Optional welcome banner. Pinned at the top of the transcript. */
  welcome?: WelcomeStats;
  /** Use the opening screen height for the Welcome panel. */
  fillWelcome?: boolean;
}

export const Messages: React.FC<MessagesProps> = ({
  committed, streaming, welcome, fillWelcome = false,
}) => {
  return (
    <>
      {welcome ? <Welcome stats={welcome} fillAvailable={fillWelcome} /> : null}
      {committed.map((turn) => <TurnRow key={turn.id} turn={turn} />)}
      {streaming ? <TurnRow turn={streaming} /> : null}
    </>
  );
};
