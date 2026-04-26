import React from 'react';
import { Static } from 'ink';
import { TurnRow, Turn } from './Turn.js';
import { Welcome, WelcomeStats } from './Welcome.js';

export interface MessagesProps {
  /** Frozen committed turns — printed once into the static region. */
  committed: Turn[];
  /** Currently-streaming assistant turn, if any. Re-renders every delta. */
  streaming?: Turn | null;
  /**
   * If provided, the welcome panel is included as the first item in the
   * static region — it then scrolls up naturally as turns grow rather
   * than disappearing on the first message.
   */
  welcome?: WelcomeStats;
  /**
   * Bumps when the terminal resizes. Used as the React key on the
   * inner Static so resizing wipes Ink's "we already printed these"
   * memo and the whole transcript re-prints fresh at the new width.
   * Without this, our resize-time clear-screen escape would leave the
   * scroll empty.
   */
  resizeNonce?: number;
}

type StaticItem =
  | { kind: 'welcome'; key: string; welcome: WelcomeStats }
  | { kind: 'turn'; key: string; turn: Turn };

export const Messages: React.FC<MessagesProps> = ({
  committed, streaming, welcome, resizeNonce = 0,
}) => {
  const items: StaticItem[] = [];
  if (welcome) {
    items.push({ kind: 'welcome', key: '__welcome__', welcome });
  }
  for (const t of committed) {
    items.push({ kind: 'turn', key: t.id, turn: t });
  }

  // Re-mount Static on resize so it re-prints the whole transcript at
  // the new width. Ink otherwise considers the previously-printed items
  // permanent and skips them, which combined with our resize-time
  // clear-screen leaves a blank scroll above the input box.
  return (
    <>
      <Static key={`static-${resizeNonce}`} items={items}>
        {(item) =>
          item.kind === 'welcome' ? (
            <Welcome key={item.key} stats={item.welcome} />
          ) : (
            <TurnRow key={item.key} turn={item.turn} />
          )
        }
      </Static>
      {streaming ? <TurnRow turn={streaming} /> : null}
    </>
  );
};
