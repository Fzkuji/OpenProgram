"use client";

/**
 * AvatarPicker — the customisation UI shown on the settings page.
 *
 * Drives an ``AvatarConfig`` (passed in via ``value``) through three
 * cascaded controls:
 *
 *   1. **Source picker** — single row of preview tiles for each
 *      DiceBear style + Letter + Custom upload. Selected tile gets a
 *      subtle amber-tinted border so the user can see the choice
 *      without reading the labels.
 *   2. **Seed input** (DiceBear sources only) — free-text + a
 *      randomise button. The seed string fully determines which glyph
 *      the chosen style renders.
 *   3. **Upload input** (Custom source only) — file picker wired to
 *      ``fileToDataUrl``. Animated GIF / WebP play in place.
 *
 * Letter-mode initial + colour pickers are NOT rendered here — they
 * live on ``AgentProfilePrefs`` (display name + initial + base colour)
 * and are owned by the surrounding settings card. Keeping them out
 * lets this component stay reusable for any avatar surface, not just
 * the main agent profile.
 */

import { useRef, useState, type CSSProperties } from "react";

import { Avatar } from "./Avatar";
import { AVATAR_STYLES } from "./styles";
import type { AvatarConfig, AvatarStyle } from "./types";
import { UPLOAD_ACCEPT, UPLOAD_MAX_BYTES, fileToDataUrl } from "./upload";

/** What the picker offers as "one click" choices. The DiceBear
 *  styles each get their own entry; ``letter`` and ``upload`` are
 *  alternatives. Maps onto ``AvatarConfig.kind`` plus, for DiceBear,
 *  ``AvatarConfig.style``. */
export type AvatarSource = AvatarStyle | "letter" | "upload";

/** Inspect a config to figure out which source tile to highlight.
 *  ``undefined`` → ``"shapes"`` (the default DiceBear style). */
export function sourceOf(cfg: AvatarConfig | undefined): AvatarSource {
  if (!cfg) return "shapes";
  if (cfg.kind === "upload") return "upload";
  if (cfg.kind === "letter") return "letter";
  return (cfg.style ?? "shapes") as AvatarSource;
}

export interface AvatarPickerProps {
  /** Current avatar config. ``undefined`` is treated as the default
   *  DiceBear ``shapes`` seeded by ``name``. */
  value: AvatarConfig | undefined;
  /** Called when the user picks a new source, seed, or uploaded
   *  file. Always emits a complete ``AvatarConfig`` (never partial). */
  onChange: (next: AvatarConfig) => void;
  /** Display name — used both as the default seed and as the
   *  picker-tile preview seed so the user sees what their own
   *  identity looks like in each style. */
  name: string;
  /** Optional bg/letter values used to render the Letter source's
   *  preview tile and as defaults when the user picks Letter. */
  letterBg?: string;
  letterText?: string;
}

export function AvatarPicker({
  value,
  onChange,
  name,
  letterBg,
  letterText,
}: AvatarPickerProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const source = sourceOf(value);
  const isDicebear = source !== "letter" && source !== "upload";

  function pickSource(src: AvatarSource) {
    setUploadError(null);
    if (src === "letter") {
      onChange({ kind: "letter" });
      return;
    }
    if (src === "upload") {
      onChange({ kind: "upload", file: value?.file });
      return;
    }
    onChange({
      kind: "dicebear",
      style: src,
      seed: value?.seed,
    });
  }

  function updateSeed(seed: string) {
    onChange({
      kind: "dicebear",
      style: (value?.style ?? "shapes") as AvatarStyle,
      seed,
    });
  }

  function randomSeed() {
    const fresh =
      Math.random().toString(36).slice(2, 10) +
      Math.random().toString(36).slice(2, 6);
    updateSeed(fresh);
  }

  async function onFilePicked(file: File) {
    setUploadError(null);
    const r = await fileToDataUrl(file);
    if (r.ok) {
      onChange({ kind: "upload", file: r.dataUrl });
    } else {
      setUploadError(r.error);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Source picker — preview tiles for each style + letter + upload. */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, 64px)",
          gap: 10,
          maxWidth: 520,
        }}
      >
        {AVATAR_STYLES.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => pickSource(s.id)}
            title={s.hint}
            style={_pickerBtn(source === s.id)}
          >
            <Avatar
              size={40}
              name={name}
              config={{
                kind: "dicebear",
                style: s.id,
                seed: value?.seed ?? name,
              }}
            />
            <span style={_pickerLabel}>{s.label}</span>
          </button>
        ))}
        <button
          type="button"
          onClick={() => pickSource("letter")}
          title="Coloured circle with one letter"
          style={_pickerBtn(source === "letter")}
        >
          <Avatar
            size={40}
            name={name}
            config={{
              kind: "letter",
              letter: letterText,
              bg: letterBg,
            }}
          />
          <span style={_pickerLabel}>Letter</span>
        </button>
        <button
          type="button"
          onClick={() => pickSource("upload")}
          title="Upload your own PNG / JPG / SVG / GIF"
          style={_pickerBtn(source === "upload")}
        >
          {value?.kind === "upload" && value.file ? (
            <Avatar size={40} name={name} config={value} />
          ) : (
            <span
              style={{
                width: 40,
                height: 40,
                borderRadius: 9999,
                border: "1px dashed var(--border-light)",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--text-muted)",
                fontSize: 18,
              }}
              aria-hidden
            >
              +
            </span>
          )}
          <span style={_pickerLabel}>Custom</span>
        </button>
      </div>

      {/* Seed input — DiceBear only. Same seed always renders the
          same glyph for a given style, so users can dial it in or
          randomise until they like one. */}
      {isDicebear && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              type="text"
              value={value?.seed ?? name}
              placeholder={name}
              onChange={(e) => updateSeed(e.target.value)}
              style={{
                padding: "6px 10px",
                background: "var(--bg-secondary)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                color: "var(--text-primary)",
                font: "inherit",
                width: 200,
              }}
            />
            <button
              type="button"
              onClick={randomSeed}
              title="Roll a fresh random seed"
              style={_smallBtn}
            >
              ↻ Random
            </button>
          </div>
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Same seed always renders the same glyph.
          </div>
        </div>
      )}

      {/* Upload input — Custom only. Reads the chosen file to a data
          URL via ``fileToDataUrl`` and stashes it on the avatar
          config; the browser handles animated formats natively. */}
      {source === "upload" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              style={_smallBtn}
            >
              Choose file…
            </button>
            {value?.kind === "upload" && value.file && (
              <button
                type="button"
                onClick={() => onChange({ kind: "upload", file: undefined })}
                style={_smallBtn}
              >
                Clear
              </button>
            )}
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept={UPLOAD_ACCEPT}
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void onFilePicked(f);
              // Reset so picking the same filename a second time
              // still triggers onChange.
              e.target.value = "";
            }}
          />
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            PNG · JPG · SVG · GIF · WebP · APNG · max{" "}
            {UPLOAD_MAX_BYTES / 1024 / 1024} MB. Animated GIF / WebP play in
            place.
          </div>
          {uploadError && (
            <div style={{ fontSize: 12, color: "var(--accent-red)" }}>
              {uploadError}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const _pickerBtn = (selected: boolean): CSSProperties => ({
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  gap: 4,
  padding: 6,
  borderRadius: 8,
  background: selected ? "var(--bg-hover)" : "transparent",
  border: selected
    ? "1px solid color-mix(in srgb, var(--accent-orange) 50%, transparent)"
    : "1px solid var(--border)",
  cursor: "pointer",
  transition: "background-color 0.15s, border-color 0.15s",
});

const _pickerLabel: CSSProperties = {
  fontSize: 11,
  color: "var(--text-secondary)",
  fontWeight: 500,
};

const _smallBtn: CSSProperties = {
  padding: "6px 12px",
  borderRadius: 6,
  fontSize: 13,
  background: "var(--bg-hover)",
  color: "var(--text-primary)",
  border: "1px solid var(--border)",
  cursor: "pointer",
  transition: "background-color 0.15s, color 0.15s, border-color 0.15s",
};
