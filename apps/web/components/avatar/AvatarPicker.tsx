"use client";

/**
 * AvatarPicker — the customisation UI shown on the settings page.
 *
 * Three types first (Generated / Letter / Upload). Generated then
 * exposes a compact style control plus a same-style variant grid.
 * Letter edits the profile's initial + colour here. Upload keeps
 * the existing file → data-URL path.
 */

import { useRef, useState, type CSSProperties } from "react";

import { useTranslation } from "@/lib/i18n";

import { Avatar } from "./Avatar";
import { AVATAR_STYLES } from "./style-options";
import type { AvatarConfig, AvatarKind, AvatarStyle } from "./types";
import { UPLOAD_ACCEPT, UPLOAD_MAX_BYTES, fileToDataUrl } from "./upload";
import {
  randomAvatarVariants,
  type AvatarVariant,
} from "./variants";

/** The three picker types. Maps 1:1 onto ``AvatarConfig.kind``
 *  (``dicebear`` is labelled Generated in the UI). */
export type AvatarSource = AvatarKind;

/** Inspect a config to figure out which type to highlight.
 *  ``undefined`` → Generated (the default DiceBear style). */
export function sourceOf(cfg: AvatarConfig | undefined): AvatarSource {
  if (!cfg) return "dicebear";
  if (cfg.kind === "upload") return "upload";
  if (cfg.kind === "letter") return "letter";
  return "dicebear";
}

/** Sixteen stable seeds fill the first Generated batch. Strings are
 *  short + memorable so initials-style users get readable chips.
 *
 *  First batch is a fixed constant (not random) so SSR and the
 *  initial client render produce identical markup; randomisation
 *  only happens in a user-triggered click handler. */
const INITIAL_VARIANT_SEEDS = [
  "Atlas", "Bento", "Cobalt", "Drift",  "Ember", "Fjord", "Gleam", "Halo",
  "Indigo", "Juno", "Klein",  "Lumen",  "Mica",  "Nova",  "Onyx",  "Pearl",
];

const INITIAL_VARIANTS: AvatarVariant[] = INITIAL_VARIANT_SEEDS.map(
  (seed) => ({ seed, style: "shapes" }),
);

/** Variant circles stay a fixed 48px track. auto-fill + a concrete
 *  column size left-aligns the grid and never stretches tiles to
 *  fill the card. */
const VARIANT_GRID: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, 48px)",
  gap: 8,
  justifyContent: "start",
};

export interface AvatarPickerProps {
  /** Current avatar config. ``undefined`` is treated as the default
   *  DiceBear ``shapes`` seeded by ``name``. */
  value: AvatarConfig | undefined;
  /** Called when the user picks a new type, style, seed, or uploaded
   *  file. Always emits a complete ``AvatarConfig`` (never partial). */
  onChange: (next: AvatarConfig) => void;
  /** Display name — used as the default seed when a style is chosen
   *  without a current seed. */
  name: string;
  /** Letter-mode initial + colour live on the profile, not on
   *  ``AvatarConfig``. The picker edits them here. */
  letterBg?: string;
  letterText?: string;
  onLetterBgChange?: (color: string) => void;
  onLetterTextChange?: (initial: string) => void;
  /** Palette for Letter colour chips. */
  colors?: readonly string[];
}

export function AvatarPicker({
  value,
  onChange,
  name,
  letterBg,
  letterText,
  onLetterBgChange,
  onLetterTextChange,
  colors = [],
}: AvatarPickerProps) {
  const { t, text } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [variants, setVariants] = useState<AvatarVariant[]>(
    INITIAL_VARIANTS,
  );
  const [spinning, setSpinning] = useState(false);
  const [lastStyle, setLastStyle] = useState<AvatarStyle>(
    value?.style ?? "shapes",
  );

  const source = sourceOf(value);
  const style: AvatarStyle = value?.style ?? lastStyle;
  const visibleVariants = variants.map((variant) => ({
    seed: variant.seed,
    style,
  }));

  function regenerate() {
    setVariants(randomAvatarVariants(style, 16));
    setSpinning(false);
    requestAnimationFrame(() => {
      setSpinning(true);
      window.setTimeout(() => setSpinning(false), 600);
    });
  }

  function pickType(src: AvatarSource) {
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
      style,
      seed: value?.seed ?? name,
    });
  }

  function pickStyle(next: AvatarStyle) {
    setLastStyle(next);
    onChange({
      kind: "dicebear",
      style: next,
      seed: value?.seed ?? name,
    });
  }

  function pickVariant(variant: AvatarVariant) {
    onChange({
      kind: "dicebear",
      style,
      seed: variant.seed,
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
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        gap: 16,
        width: "100%",
        minWidth: 0,
      }}
    >
      <div
        role="radiogroup"
        aria-label={text("Avatar type", "头像类型")}
        style={_typeBar}
      >
        {(
          [
            { id: "dicebear", label: text("Generated", "生成") },
            { id: "letter", label: text("Letter", "字母") },
            { id: "upload", label: text("Upload", "上传") },
          ] as const
        ).map((option) => (
          <button
            key={option.id}
            type="button"
            role="radio"
            aria-checked={source === option.id}
            onClick={() => pickType(option.id)}
            className={_typeBtn(source === option.id)}
          >
            {option.label}
          </button>
        ))}
      </div>

      {source === "dicebear" && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            gap: 14,
            minWidth: 0,
          }}
        >
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-start",
              gap: 8,
            }}
          >
            <span style={_sectionHint}>{text("Style", "风格")}</span>
            <div
              role="radiogroup"
              aria-label={text("Avatar style", "头像风格")}
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 6,
                justifyContent: "flex-start",
              }}
            >
              {AVATAR_STYLES.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  role="radio"
                  aria-checked={style === s.id}
                  onClick={() => pickStyle(s.id)}
                  title={s.hint}
                  className={_styleChip(style === s.id)}
                >
                  <span style={_styleChipLabel}>{s.label}</span>
                </button>
              ))}
            </div>
          </div>

          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "flex-start",
              flexWrap: "wrap",
              gap: 8,
              minWidth: 0,
            }}
          >
            <span style={_sectionHint}>
              {text(
                "Pick a variant of this style.",
                "选择这个风格的一个变体。",
              )}
            </span>
            <button
              type="button"
              onClick={regenerate}
              title={text(
                "Generate a fresh batch of variants",
                "换一批这个风格的变体",
              )}
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
              {text("Regenerate", "换一批")}
            </button>
          </div>
          <div style={VARIANT_GRID}>
            {visibleVariants.map((variant) => {
              const selected =
                (value?.style ?? "shapes") === variant.style &&
                (value?.seed ?? name) === variant.seed;
              const styleLabel =
                AVATAR_STYLES.find((entry) => entry.id === variant.style)
                  ?.label ?? variant.style;
              return (
                <button
                  key={`${variant.style}:${variant.seed}`}
                  type="button"
                  onClick={() => pickVariant(variant)}
                  title={`${styleLabel}: ${variant.seed}`}
                  aria-label={`Use ${styleLabel} avatar variant`}
                  className={_variantTile(selected)}
                >
                  <Avatar
                    size={40}
                    name={variant.seed}
                    config={{
                      kind: "dicebear",
                      style: variant.style,
                      seed: variant.seed,
                    }}
                  />
                </button>
              );
            })}
          </div>
        </div>
      )}

      {source === "letter" && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            gap: 12,
          }}
        >
          <div>
            <label style={_fieldLabel} htmlFor="avatar-letter-initial">
              {t("general.agent.initial")}
            </label>
            <input
              id="avatar-letter-initial"
              type="text"
              value={letterText ?? ""}
              maxLength={2}
              onChange={(e) => onLetterTextChange?.(e.target.value)}
              style={_initialInput}
            />
            <div style={_sectionHint}>{t("general.agent.initial.hint")}</div>
          </div>
          <div>
            <div style={_fieldLabel}>{t("general.agent.color")}</div>
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 6,
                justifyContent: "flex-start",
              }}
            >
              {colors.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => onLetterBgChange?.(c)}
                  aria-label={c}
                  title={c}
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: 6,
                    background: c,
                    border:
                      letterBg === c
                        ? "2px solid var(--text-primary)"
                        : "1px solid var(--border)",
                    cursor: "pointer",
                    padding: 0,
                  }}
                />
              ))}
            </div>
          </div>
        </div>
      )}

      {source === "upload" && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            gap: 6,
          }}
        >
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className={_smallBtnCls}
            >
              {text("Choose file…", "选择文件…")}
            </button>
            {value?.kind === "upload" && value.file && (
              <button
                type="button"
                onClick={() => onChange({ kind: "upload", file: undefined })}
                className={_smallBtnCls}
              >
                {text("Clear", "清除")}
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
              e.target.value = "";
            }}
          />
          <div style={_sectionHint}>
            PNG · JPG · SVG · GIF · WebP · APNG · max{" "}
            {UPLOAD_MAX_BYTES / 1024 / 1024} MB. Animated GIF / WebP play in
            place.
          </div>
          {uploadError && (
            <div style={{ fontSize: "var(--fs-sm)", color: "var(--accent-red)" }}>
              {uploadError}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const _typeBar: CSSProperties = {
  display: "inline-flex",
  background: "var(--bg-secondary)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  padding: 2,
  gap: 2,
};

const _typeBtn = (selected: boolean): string =>
  "inline-flex items-center justify-center h-7 rounded-md px-3 text-fs-sm font-medium border-0 cursor-pointer transition-colors " +
  (selected
    ? "bg-[var(--bg-hover)] text-[var(--text-bright)]"
    : "bg-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]");

const _styleChip = (selected: boolean): string =>
  "inline-flex items-center justify-center h-7 rounded-md px-2.5 border cursor-pointer transition-colors " +
  (selected
    ? "bg-[var(--bg-hover)] border-[color-mix(in_srgb,var(--accent-orange)_50%,transparent)]"
    : "border-[var(--border)] hover:bg-[var(--bg-hover)] hover:border-[color-mix(in_srgb,var(--accent-orange)_30%,transparent)]");

const _styleChipLabel: CSSProperties = {
  fontSize: "var(--fs-sm)",
  fontFamily: "var(--font-sans)",
  color: "var(--text-secondary)",
  fontWeight: 500,
  lineHeight: 1.2,
  whiteSpace: "nowrap",
  overflowWrap: "normal",
  wordBreak: "normal",
  hyphens: "none",
};

const _variantTile = (selected: boolean): string =>
  "inline-flex items-center justify-center w-12 h-12 p-0 rounded-full border cursor-pointer transition-colors " +
  (selected
    ? "bg-[var(--bg-hover)] border-[color-mix(in_srgb,var(--accent-orange)_50%,transparent)]"
    : "border-[var(--border)] hover:bg-[var(--bg-hover)] hover:border-[color-mix(in_srgb,var(--accent-orange)_30%,transparent)]");

const _sectionHint: CSSProperties = {
  fontSize: "var(--fs-sm)",
  fontFamily: "var(--font-sans)",
  color: "var(--text-muted)",
  lineHeight: 1.4,
};

const _fieldLabel: CSSProperties = {
  display: "block",
  fontSize: 14,
  fontFamily: "var(--font-sans)",
  color: "var(--text-primary)",
  marginBottom: 6,
};

const _initialInput: CSSProperties = {
  padding: "6px 10px",
  background: "var(--bg-secondary)",
  border: "1px solid var(--border)",
  borderRadius: "var(--ui-button-radius)",
  color: "var(--text-primary)",
  font: "inherit",
  width: 64,
  textAlign: "center",
};

const _smallBtnCls =
  "inline-flex items-center justify-center h-7 rounded-md px-3 text-fs-sm font-medium border border-[var(--border)] bg-[var(--bg-hover)] text-[var(--text-secondary)] cursor-pointer transition-colors hover:bg-[color-mix(in_srgb,var(--accent-orange)_18%,transparent)] hover:text-[var(--accent-orange)] hover:border-[color-mix(in_srgb,var(--accent-orange)_30%,transparent)]";
