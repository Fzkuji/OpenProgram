"use client";

/**
 * A demonstration recording, as the user sees it.
 *
 * `RecordingTile` sits in the composer until the message is sent;
 * `RecordingCard` replaces the `<recorded-demonstration>` block in a sent
 * message and expands into the step list with thumbnails. The block's
 * text is for the model and is never shown.
 */
import { useState } from "react";
import { ChevronRight } from "lucide-react";

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
  const [rows, setRows] = useState<StepRow[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [viewing, setViewing] = useState<string | null>(null);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && rows === null) {
      fetch(`/api/recordings/${encodeURIComponent(info.id)}/steps`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
        .then((body) => setRows(Array.isArray(body.rows) ? body.rows : []))
        .catch(() => setFailed(true));
    }
  };

  const image = (name: string) => `/api/recordings/${encodeURIComponent(info.id)}/images/${encodeURIComponent(name)}`;

  return (
    <div className={styles.card} data-recording-id={info.id}>
      <button type="button" className={styles.cardHead} aria-expanded={open} onClick={toggle}>
        <span className={styles.glyph}><RecordingGlyph /></span>
        <span className="min-w-0 flex-1">
          <span className={styles.cardTitle}>{text("Recorded demonstration", "录制的演示")}</span>
          <span className={styles.cardMeta}>{meta(info)}</span>
        </span>
        <ChevronRight className={`${styles.chevron} ${open ? styles.chevronOpen : ""} size-4`} aria-hidden="true" />
      </button>
      {open && failed ? (
        <div className={styles.note}>
          {text("The recording is no longer on this computer.", "这台电脑上已经没有这个录制了。")}
        </div>
      ) : null}
      {open && !failed && rows === null ? (
        <div className={styles.note}>{text("Loading…", "加载中…")}</div>
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
