"use client";

/**
 * A demonstration recording, as the user sees it.
 *
 * `RecordingTile` sits in the composer until the message is sent;
 * `RecordingCard` replaces the `<recorded-demonstration>` block in a sent
 * message and expands into the step list with thumbnails. The block's
 * text is for the model and is never shown.
 */
import { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import { isPermanent, retryDelay } from "@/lib/abilities/kept-read";

import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useTranslation } from "@/lib/i18n";
import { formatDuration } from "@/lib/recording/recording-store";
import stripStyles from "../composer/attach/strip/attachment-strip.module.css";
import { RecordingGlyph } from "./recording-glyph";
import styles from "./recording.module.css";

export interface RecordingInfo {
  id: string;
  steps: number;
  duration_ms: number;
  images: number;
  /** The block as sent, kept when the message goes back to the composer. */
  raw?: string;
}

interface StepRow {
  t: number;
  type: string;
  text: string;
  image: string | null;
}

interface StepsReply { rows?: unknown }

/** A recording's steps, read once per recording and kept: "gone" when the
 *  worker no longer has it, "refused" for any other 4xx (nothing is drawn).
 *  A read that fails otherwise is forgotten, so the next look asks again. */
type Steps = StepRow[] | "gone" | "refused";
const readSteps = new Map<string, Steps>();
const pendingSteps = new Map<string, Promise<Steps | null>>();

function isStepRow(row: unknown): row is StepRow {
  if (!row || typeof row !== "object") return false;
  const { t, type, text, image } = row as Record<string, unknown>;
  return typeof t === "number" && typeof type === "string" && typeof text === "string"
    && (image === null || typeof image === "string");
}

/** Start reading a recording's steps (on hover, before the click). */
export function readRecordingSteps(id: string): Promise<Steps | null> {
  const held = readSteps.get(id);
  if (held) return Promise.resolve(held);
  let pending = pendingSteps.get(id);
  if (!pending) {
    pending = fetch(`/api/recordings/${encodeURIComponent(id)}/steps`)
      .then(async (response): Promise<Steps | null> => {
        if (response.status === 404) return "gone";
        if (isPermanent({ status: response.status })) return "refused";
        if (!response.ok) throw new Error(String(response.status));
        const body = await response.json() as StepsReply;
        return Array.isArray(body.rows) ? body.rows.filter(isStepRow) : [];
      })
      .then((steps) => { if (steps) readSteps.set(id, steps); return steps; })
      .catch((error: unknown) => { console.warn("recording steps read failed", { id, error }); return null; })
      .finally(() => pendingSteps.delete(id));
    pendingSteps.set(id, pending);
  }
  return pending;
}

function useMeta() {
  const { text } = useTranslation();
  return (info: RecordingInfo) => [
    text(`${info.steps} ${info.steps === 1 ? "step" : "steps"}`, `${info.steps} 步`),
    formatDuration(info.duration_ms),
    text(`${info.images} ${info.images === 1 ? "image" : "images"}`, `${info.images} 张图`),
  ].join(" · ");
}

function clock(ms: number): string {
  const seconds = Math.max(0, ms) / 1000;
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;
}

/** The finished recording waiting in the composer. */
export function RecordingTile({ info, onRemove }: { info: RecordingInfo; onRemove(): void }) {
  const { text } = useTranslation();
  const meta = useMeta();
  const name = text("Recorded demonstration", "录制的演示");
  return (
    <div className={stripStyles.attachmentStrip} data-recording-strip>
      <div className={stripStyles.attachmentItem} data-recording-id={info.id}>
        <span className={stripStyles.preview} style={{ cursor: "default" }}>
          <span className={styles.glyph}><RecordingGlyph /></span>
          <span className={stripStyles.copy}>
            <span className={stripStyles.name}>{name}</span>
            <span className={stripStyles.meta}>{meta(info)}</span>
          </span>
        </span>
        <button
          type="button"
          className={stripStyles.remove}
          aria-label={`${text("Remove", "移除")} ${name}`}
          onClick={onRemove}
        >
          ×
        </button>
      </div>
    </div>
  );
}

/** The recording in a sent message. */
export function RecordingCard({ info }: { info: RecordingInfo }) {
  const { text } = useTranslation();
  const meta = useMeta();
  const [open, setOpen] = useState(false);
  const [steps, setSteps] = useState<Steps | null>(() => readSteps.get(info.id) ?? null);
  const [viewing, setViewing] = useState<string | null>(null);

  // While open, a read that failed is asked again, quietly, with backoff.
  useEffect(() => {
    if (!open || steps !== null) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = (attempt: number) => {
      void readRecordingSteps(info.id).then((answer) => {
        if (!live) return;
        if (answer) setSteps(answer);
        else timer = setTimeout(() => read(attempt + 1), retryDelay(attempt));
      });
    };
    read(0);
    return () => { live = false; if (timer) clearTimeout(timer); };
  }, [open, steps, info.id]);

  const rows = Array.isArray(steps) ? steps : null;

  const image = (name: string) => `/api/recordings/${encodeURIComponent(info.id)}/images/${encodeURIComponent(name)}`;

  return (
    <div className={styles.card} data-recording-id={info.id}>
      <button
        type="button"
        className={styles.cardHead}
        aria-expanded={open}
        onPointerEnter={() => { if (steps === null) void readRecordingSteps(info.id); }}
        onClick={() => setOpen(!open)}
      >
        <span className={styles.glyph}><RecordingGlyph /></span>
        <span className="min-w-0 flex-1">
          <span className={styles.cardTitle}>{text("Recorded demonstration", "录制的演示")}</span>
          <span className={styles.cardMeta}>{meta(info)}</span>
        </span>
        <ChevronRight className={`${styles.chevron} ${open ? styles.chevronOpen : ""} size-4`} aria-hidden="true" />
      </button>
      {open && steps === "gone" ? (
        <div className={styles.note}>
          {text("The recording is no longer on this computer.", "这台电脑上已经没有这个录制了。")}
        </div>
      ) : null}
      {open && rows ? (
        <ol className={styles.steps}>
          {rows.map((row, index) => (
            <li key={index} className={styles.step}>
              <span className={styles.time}>{clock(row.t)}</span>
              <span className={row.type === "say" ? styles.said : undefined}>{row.text}</span>
              {row.image ? (
                <button
                  type="button"
                  className={styles.thumbButton}
                  aria-label={text("Show screenshot", "查看截图")}
                  onClick={() => setViewing(row.image)}
                >
                  <img src={image(row.image)} alt="" loading="lazy" />
                </button>
              ) : <span />}
            </li>
          ))}
        </ol>
      ) : null}
      {viewing ? (
        <Dialog open onOpenChange={(next) => { if (!next) setViewing(null); }}>
          <DialogContent className={styles.viewer}>
            <DialogTitle className="sr-only">{text("Screenshot", "截图")}</DialogTitle>
            <img src={image(viewing)} alt={text("Screenshot from the recording", "录制中的截图")} />
          </DialogContent>
        </Dialog>
      ) : null}
    </div>
  );
}
