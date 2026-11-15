import { SOLAR_BODIES } from "@/components/solar-icons/bodies";
import styles from "./agent-cursor.module.css";

// The app's own icon set's two-tone cursor (solar:cursor, 24×24), laid on a
// white keyline cut from the same shape in one piece (solar:cursor-bold), so
// the lighter tail reads against white. Its tip sits at about (4.2, 4.2).
const GLYPH = `<g data-keyline="true">${SOLAR_BODIES["cursor-solid"]}</g>${SOLAR_BODIES.cursor}`;

/**
 * The agent's pointer: the two-tone Solar cursor glyph in the agent accent
 * on a white keyline, plus a one-shot press that leaves a small dot where it
 * clicked. The point is the centre of its 40px box; the tip rests 2px below
 * and right of it, so the dot shows beside the arrow. The box is
 * placed centred on the action point. Display only: it never takes
 * pointer events. The accent comes from `--agent-accent`, which follows the
 * theme.
 */
export function AgentCursor({
  play,
  playSeq = 0,
}: {
  play: "once" | "never";
  playSeq?: number;
}) {
  return (
    <span className={styles.agentCursor} data-agent-cursor="true" aria-hidden="true">
      {play === "once" ? <span key={`d${playSeq}`} className={styles.dot} data-agent-click-dot="true" /> : null}
      <span key={`a${play === "once" ? playSeq : 0}`} className={play === "once" ? `${styles.arrow} ${styles.press}` : styles.arrow}>
        <svg viewBox="0 0 24 24" width={26} height={26} focusable="false" dangerouslySetInnerHTML={{ __html: GLYPH }} />
      </span>
    </span>
  );
}
