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
   * Welcome stats. Rendered as the first <Static> item so it scrolls
   * naturally with the transcript and stays visible above the first chat
   * turns. It does NOT auto-dismiss when the user sends a message —
   * users explicitly asked for the welcome panel to stay put.
   */
  welcome?: WelcomeStats;
  /**
   * Bumps when the active theme changes. Used as the React key on
   * <Static> so Ink's "we already printed these" memo is invalidated and
   * the whole transcript re-prints fresh in the new palette.
   *
   * Resize is intentionally NOT folded into this key. Ink handles resize
   * internally (its own listener recomputes the Yoga layout and triggers
   * onRender); us also clearing the screen + re-keying Static races
   * against Ink's log-update internal state and produces blank-screen
   * artifacts at certain widths. Trust Ink for resize.
   */
  themeNonce?: number;
}

type StaticItem =
  | { kind: 'welcome'; key: string; welcome: WelcomeStats }
  | { kind: 'turn'; key: string; turn: Turn };

export const Messages: React.FC<MessagesProps> = ({
  committed, streaming, welcome, themeNonce = 0,
}) => {
  const items: StaticItem[] = [];
  if (welcome) {
    items.push({ kind: 'welcome', key: '__welcome__', welcome });
  }
  for (const t of committed) {
    items.push({ kind: 'turn', key: t.id, turn: t });
  }

  return (
    <>
      <Static key={`static-${themeNonce}`} items={items}>
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
