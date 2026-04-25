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
}

type StaticItem =
  | { kind: 'welcome'; key: string; welcome: WelcomeStats }
  | { kind: 'turn'; key: string; turn: Turn };

export const Messages: React.FC<MessagesProps> = ({ committed, streaming, welcome }) => {
  const items: StaticItem[] = [];
  if (welcome) {
    items.push({ kind: 'welcome', key: '__welcome__', welcome });
  }
  for (const t of committed) {
    items.push({ kind: 'turn', key: t.id, turn: t });
  }

  return (
    <>
      <Static items={items}>
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
