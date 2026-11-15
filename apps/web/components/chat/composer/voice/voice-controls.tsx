"use client";

/**
 * The composer's voice input pieces: a round microphone button in the
 * bottom-left corner of the input box (mirroring the send button in the
 * bottom-right),
 * and, while recording, a live strip before the send button — a dot for
 * every quiet moment, a bar sized by loudness while you speak, newest on
 * the right. Engine, model and language live in Settings → Voice only.
 */
import { platformKeys, useIsMacPlatform } from "@/lib/shortcut-keys";
import { buttonVariants } from "@/components/ui/button";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { SolarIcon } from "@/components/solar-icons";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

import type { VoiceLevel, VoiceState } from "./use-voice-input";
import styles from "./voice.module.css";

export function MicButton({ state, onToggle }: { state: VoiceState; onToggle: () => void }) {
  const { text } = useTranslation();
  const mac = useIsMacPlatform();
  const active = state === "recording" || state === "starting";
  const label = active
    ? text("Stop voice input", "停止语音输入")
    : state === "finishing"
      ? text("Transcribing…", "正在转写…")
      : text("Voice input", "语音输入");
  return (
    <HoverTip
      label={
        <TipBody
          title={label}
          detail={active
            ? text("Esc discards this recording.", "按 Esc 放弃这段录音。")
            : text(`Click, or hold ${platformKeys("⌥", mac)}Space while typing. Text stays in the box to edit.`,
              `点击，或在输入时按住 ${platformKeys("⌥", mac)}空格。文字留在输入框里，可以改完再发。`)}
        />
      }
    >
      <button
        type="button"
        className={cn(buttonVariants({ variant: "ghost", size: "icon-sm" }), styles.micBtn)}
        data-state={state}
        aria-pressed={active}
        aria-label={label}
        disabled={state === "finishing"}
        onMouseDown={(event) => event.preventDefault()}
        onClick={onToggle}
      >
        <SolarIcon name="microphone-2" size={16} fit="fill" aria-hidden="true" />
      </button>
    </HoverTip>
  );
}

export function VoiceStrip({ state, levels }: { state: VoiceState; levels: VoiceLevel[] }) {
  const { text } = useTranslation();
  if (state === "idle") return null;
  if (state === "finishing") {
    return (
      <div className={styles.strip} role="status">
        <span className={styles.finishing}>{text("Transcribing…", "正在转写…")}</span>
      </div>
    );
  }
  return (
    <div className={styles.strip} role="status" aria-label={text("Listening", "正在听")}>
      {levels.map((sample, index) =>
        sample.speaking ? (
          <i key={index} className={styles.bar} style={{ height: `${4 + sample.level * 14}px` }} />
        ) : (
          <i key={index} className={styles.dot} />
        ),
      )}
    </div>
  );
}
