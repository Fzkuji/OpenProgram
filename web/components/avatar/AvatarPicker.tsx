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

/** Fixed sample seed used in the style picker tiles. Some DiceBear
 *  styles (notionists, lorelei) sometimes generate avatars with
 *  near-transparent backgrounds for arbitrary seeds — the tile then
 *  looked empty on a dark page. ``Sample`` deterministically produces
 *  a visible glyph in every shipped style, so the tile always reads
 *  as "this is what this style looks like". The user's own seed
 *  drives the variant grid below, not the style tiles. */
const STYLE_PICKER_SEED = "Sample";

/** Initial variant seeds shown as a grid for the currently-selected
 *  DiceBear style. 12 stable strings give the user "browse and pick"
 *  semantics without the dice-roll feel of the old Random button.
 *  Strings are intentionally short + memorable so initials-style users
 *  get readable two-letter chips when this style is active.
 *
 *  This is the FIRST batch only — the Regenerate (↻) button next to
 *  the variant grid swaps in a fresh batch of random seeds. Keeping
 *  the first batch a fixed constant (not random) means SSR and the
 *  initial client render produce identical markup; randomisation only
 *  happens in a user-triggered click handler, never during render. */
const INITIAL_VARIANT_SEEDS = [
  "Atlas", "Bento", "Cobalt", "Drift", "Ember", "Fjord",
  "Gleam", "Halo",  "Indigo", "Juno",  "Klein", "Lumen",
];

/** Build a fresh batch of N random seed strings for the Regenerate
 *  button. Short base36 chunks — readable and collision-free enough
 *  for a 12-item grid. */
function _randomVariantSeeds(n: number): string[] {
  return Array.from({ length: n }, () =>
    Math.random().toString(36).slice(2, 9),
  );
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
  // The set of seeds the variant grid currently offers. Starts as the
  // fixed first batch; the ↻ button replaces it with random seeds.
  const [variantSeeds, setVariantSeeds] = useState<string[]>(
    INITIAL_VARIANT_SEEDS,
  );
  // Drives the one-shot spin of the ↻ glyph on each regenerate click.
  const [spinning, setSpinning] = useState(false);

  const source = sourceOf(value);
  const isDicebear = source !== "letter" && source !== "upload";

  function regenerate() {
    setVariantSeeds(_randomVariantSeeds(12));
    // Restart the spin: drop to false this frame, raise next frame so
    // the CSS animation re-triggers even on rapid repeat clicks.
    setSpinning(false);
    requestAnimationFrame(() => {
      setSpinning(true);
      window.setTimeout(() => setSpinning(false), 600);
    });
  }

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

  function pickVariant(seed: string) {
    onChange({
      kind: "dicebear",
      style: (value?.style ?? "shapes") as AvatarStyle,
      seed,
    });
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
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Source picker — each tile shows the same fixed sample seed
          so every style renders something visible (avoids the
          empty-tile bug where the user's current seed happened to
          produce a near-blank notionists / lorelei glyph). The
          tile is "what does THIS STYLE look like", not "what does
          MY identity look like in this style". */}
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
            className={_pickerTile(source === s.id)}
          >
            <Avatar
              size={40}
              name={STYLE_PICKER_SEED}
              config={{
                kind: "dicebear",
                style: s.id,
                seed: STYLE_PICKER_SEED,
              }}
            />
            <span style={_pickerLabel}>{s.label}</span>
          </button>
        ))}
        <button
          type="button"
          onClick={() => pickSource("letter")}
          title="Coloured circle with one letter"
          className={_pickerTile(source === "letter")}
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
          className={_pickerTile(source === "upload")}
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

      {/* Variant grid — when a DiceBear style is active, show a batch
          of seeds rendered IN THAT STYLE. The user clicks the one
          they like; that seed becomes their avatar. The ↻ button to
          the right of the caption swaps in a fresh batch of random
          seeds, so "browse and pick" can keep going until something
          clicks — without the dice-roll-into-the-void feel of the
          old single Random button. */}
      {isDicebear && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 8,
              maxWidth: 392,
            }}
          >
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              Pick a variant — each renders the same style with a
              different seed.
            </span>
            <button
              type="button"
              onClick={regenerate}
              title="Generate a fresh batch of variants"
              className={_smallBtnCls}
            >
              <span
                style={{
                  display: "inline-block",
                  animation: spinning ? "avatarSpin 0.6s linear" : "none",
                }}
              >
                ↻
              </span>
              Regenerate
            </button>
          </div>
          <div
            style={{
              display: "grid",
              // Exactly 6 columns so the 12-seed batch fills two even
              // rows (6 × 2) instead of wrapping to a ragged 8 + 4.
              gridTemplateColumns: "repeat(6, 56px)",
              gap: 8,
              maxWidth: 392,
            }}
          >
            {variantSeeds.map((seed) => {
              const selected = (value?.seed ?? name) === seed;
              return (
                <button
                  key={seed}
                  type="button"
                  onClick={() => pickVariant(seed)}
                  title={seed}
                  className={_variantTile(selected)}
                >
                  <Avatar
                    size={40}
                    name={seed}
                    config={{
                      kind: "dicebear",
                      style: (value?.style ?? "shapes") as AvatarStyle,
                      seed,
                    }}
                  />
                </button>
              );
            })}
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
              className={_smallBtnCls}
            >
              Choose file…
            </button>
            {value?.kind === "upload" && value.file && (
              <button
                type="button"
                onClick={() => onChange({ kind: "upload", file: undefined })}
                className={_smallBtnCls}
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

// All buttons here use Tailwind className strings (not inline style
// objects) for one reason: inline styles can't express ``:hover``, so
// the old inline-style buttons had zero hover feedback — which is what
// made the Regenerate button feel dead and off-system. These mirror
// the idle-neutral / amber-on-hover pill philosophy used across the
// app (skills discovery, settings), with ``transition-colors`` for
// the animation.

// Style + Letter + Custom tiles: column layout (avatar over label),
// idle transparent, amber border when selected, subtle hover when not.
const _pickerTile = (selected: boolean): string =>
  "flex flex-col items-center gap-1 p-1.5 rounded-lg border cursor-pointer transition-colors " +
  (selected
    ? "bg-[var(--bg-hover)] border-[color-mix(in_srgb,var(--accent-orange)_50%,transparent)]"
    : "border-[var(--border)] hover:bg-[var(--bg-hover)] hover:border-[color-mix(in_srgb,var(--accent-orange)_30%,transparent)]");

// Variant tile — fixed 56×56, no label, denser grid. Same idle/
// selected/hover treatment as the style tiles.
const _variantTile = (selected: boolean): string =>
  "inline-flex items-center justify-center w-14 h-14 p-1 rounded-lg border cursor-pointer transition-colors " +
  (selected
    ? "bg-[var(--bg-hover)] border-[color-mix(in_srgb,var(--accent-orange)_50%,transparent)]"
    : "border-[var(--border)] hover:bg-[var(--bg-hover)] hover:border-[color-mix(in_srgb,var(--accent-orange)_30%,transparent)]");

const _pickerLabel: CSSProperties = {
  fontSize: 11,
  color: "var(--text-secondary)",
  fontWeight: 500,
};

// Small action button (Regenerate / Choose file / Clear). Fixed 28px
// height (h-7) so it no longer towers over its caption, rounded, and
// — crucially — an actual hover transition into the amber accent.
const _smallBtnCls =
  "inline-flex items-center justify-center h-7 rounded-md px-3 text-[12px] font-medium border border-[var(--border)] bg-[var(--bg-hover)] text-[var(--text-secondary)] cursor-pointer transition-colors hover:bg-[color-mix(in_srgb,var(--accent-orange)_18%,transparent)] hover:text-[var(--accent-orange)] hover:border-[color-mix(in_srgb,var(--accent-orange)_30%,transparent)]";
