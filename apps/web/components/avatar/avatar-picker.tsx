"use client";

/**
 * AvatarPicker — the customisation UI shown on the settings page.
 *
 * Types first (Generated / Letter / Upload, and Character where the
 * caller offers it). Generated then exposes a compact style control plus
 * a same-style variant grid. Letter edits the profile's initial + colour
 * here. Upload keeps the existing file → data-URL path. Character picks a
 * Bot shape and colour.
 */

import { useRef, useState, type CSSProperties } from "react";

import { RefreshCw } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldTitle,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Toggle } from "@/components/ui/toggle";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

import { Avatar } from "./avatar";
import { BOT_COLORS, BOT_COLOR_IDS, BOT_SHAPES, isBotColor, isBotShape, type BotColor, type BotShape } from "./bot/catalog";
import { botLookFor } from "./bot/identity";
import { AVATAR_STYLES } from "./style-options";
import type { AvatarConfig, AvatarKind, AvatarStyle } from "./types";
import { UPLOAD_ACCEPT, UPLOAD_MAX_BYTES, fileToDataUrl, formatBytes } from "./upload";
import {
  randomAvatarVariants,
  type AvatarVariant,
} from "./variants";

/** The picker types. Maps 1:1 onto ``AvatarConfig.kind``
 *  (``dicebear`` is labelled Generated and ``bot`` Character in the UI). */
export type AvatarSource = AvatarKind;

/** What the profile picker offers. Agents pass their own list. */
const DEFAULT_SOURCES: readonly AvatarSource[] = ["dicebear", "letter", "upload"];

/** Inspect a config to figure out which type to highlight.
 *  ``undefined`` → Generated (the default DiceBear style). */
export function sourceOf(cfg: AvatarConfig | undefined): AvatarSource {
  if (!cfg) return "dicebear";
  if (cfg.kind === "upload") return "upload";
  if (cfg.kind === "letter") return "letter";
  if (cfg.kind === "bot") return "bot";
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
  width: "100%",
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
  /** The types on offer, in order. Defaults to Generated, Letter, Upload. */
  sources?: readonly AvatarSource[];
  /** What a character is derived from when no shape or colour was picked
   *  (an Agent's id). Defaults to ``name``. */
  seed?: string;
  /** Largest upload accepted. Defaults to ``UPLOAD_MAX_BYTES``. */
  uploadMaxBytes?: number;
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
  sources = DEFAULT_SOURCES,
  seed,
  uploadMaxBytes = UPLOAD_MAX_BYTES,
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

  const source = value ? sourceOf(value) : sources[0];
  const style: AvatarStyle = value?.style ?? lastStyle;
  const derivedLook = botLookFor(seed ?? name);
  const botShape: BotShape = isBotShape(value?.shape) ? value.shape : derivedLook.shape;
  const botColor: BotColor = isBotColor(value?.color) ? value.color : derivedLook.color;
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
    if (src === "bot") {
      onChange({ kind: "bot", shape: botShape, color: botColor });
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
    const r = await fileToDataUrl(file, uploadMaxBytes);
    if (r.ok) {
      onChange({ kind: "upload", file: r.dataUrl });
    } else {
      setUploadError(r.error);
    }
  }

  return (
    <div className="flex w-[440px] max-w-full min-w-0 flex-col items-start gap-4">
      <ToggleGroup
        type="single"
        variant="elevated"
                value={source}
        onValueChange={(next) => {
          if (next) pickType(next as AvatarSource);
        }}
        aria-label={text("Avatar type", "头像类型")}
      >
        {sources.map((entry) => (
          <ToggleGroupItem key={entry} value={entry}>
            {entry === "bot" ? text("Character", "角色")
              : entry === "dicebear" ? text("Generated", "生成")
              : entry === "letter" ? text("Letter", "字母")
              : text("Upload", "上传")}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>

      {source === "bot" && (
        <div className="flex w-full min-w-0 flex-col items-start gap-4">
          <div className="flex w-full flex-col items-start gap-2">
            <FieldDescription>{text("Shape", "形状")}</FieldDescription>
            <div style={VARIANT_GRID}>
              {BOT_SHAPES.map((shape) => (
                <Toggle
                  key={shape}
                  variant="elevated"
                  pressed={shape === botShape}
                  onPressedChange={() => onChange({ kind: "bot", shape, color: botColor })}
                  aria-label={text(`Use the ${shape} character`, `使用 ${shape} 角色`)}
                  className="size-12 p-0 data-[state=on]:border-ring data-[state=on]:ring-[3px] data-[state=on]:ring-ring/50"
                >
                  <Avatar size={34} name={shape} decorative config={{ kind: "bot", shape, color: botColor }} />
                </Toggle>
              ))}
            </div>
          </div>
          <div className="flex w-full flex-col items-start gap-2">
            <FieldDescription>{text("Colour", "颜色")}</FieldDescription>
            <ToggleGroup
              type="single"
              className="flex-wrap"
              value={botColor}
              onValueChange={(next) => {
                if (isBotColor(next)) onChange({ kind: "bot", shape: botShape, color: next });
              }}
              aria-label={text("Character colour", "角色颜色")}
            >
              {BOT_COLOR_IDS.map((id) => (
                <ToggleGroupItem
                  key={id}
                  value={id}
                  aria-label={id}
                  className="size-(--ui-button-h-xs) min-w-0 border border-input p-0 data-[state=on]:ring-[3px] data-[state=on]:ring-ring/50"
                  style={{ background: BOT_COLORS[id] }}
                />
              ))}
            </ToggleGroup>
          </div>
        </div>
      )}

      {source === "dicebear" && (
        <div className="flex w-full min-w-0 flex-col items-start gap-4">
          <div className="flex w-full flex-col items-start gap-2">
            <FieldDescription>{text("Style", "风格")}</FieldDescription>
            <ToggleGroup
              type="single"
              variant="elevated"
              className="w-full flex-wrap"
              value={style}
              onValueChange={(next) => {
                if (next) pickStyle(next as AvatarStyle);
              }}
              aria-label={text("Avatar style", "头像风格")}
            >
              {AVATAR_STYLES.map((s) => (
                <ToggleGroupItem key={s.id} value={s.id}>
                  {s.label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>

          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <FieldDescription>
              {text("Pick a variant of this style.", "选择这个风格的一个变体。")}
            </FieldDescription>
            <Button type="button" variant="elevated" onClick={regenerate}>
              <RefreshCw
                data-icon="inline-start"
                style={{ animation: spinning ? "avatarSpin 0.6s linear" : "none" }}
              />
              {text("Regenerate", "换一批")}
            </Button>
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
                <Toggle
                  key={`${style}:${variant.seed}`}
                  variant="elevated"
                  pressed={selected}
                  onPressedChange={() => pickVariant(variant)}
                  aria-label={`Use ${styleLabel} avatar variant`}
                  className="size-12 rounded-full p-0 data-[state=on]:border-ring data-[state=on]:ring-[3px] data-[state=on]:ring-ring/50"
                >
                  <Avatar
                    size={40}
                    name={variant.seed}
                    config={{
                      kind: "dicebear",
                      style,
                      seed: variant.seed,
                    }}
                  />
                </Toggle>
              );
            })}
          </div>
        </div>
      )}

      {source === "letter" && (
        <FieldGroup className="w-full gap-4">
          <Field>
            <FieldLabel htmlFor="avatar-letter-initial">
              {t("general.avatar.initial")}
            </FieldLabel>
            <Input
              id="avatar-letter-initial"
              type="text"
              value={letterText ?? ""}
              maxLength={2}
              onChange={(e) => onLetterTextChange?.(e.target.value)}
              className="w-16 text-center"
            />
            <FieldDescription>{t("general.avatar.initial.hint")}</FieldDescription>
          </Field>
          <Field>
            <FieldTitle>{t("general.avatar.color")}</FieldTitle>
            <ToggleGroup
              type="single"
              className="flex-wrap"
              value={letterBg ?? ""}
              onValueChange={(next) => {
                if (next) onLetterBgChange?.(next);
              }}
              aria-label={t("general.avatar.color")}
            >
              {colors.map((c) => (
                <ToggleGroupItem
                  key={c}
                  value={c}
                  aria-label={c}
                  className="size-(--ui-button-h-xs) min-w-0 border border-input p-0 data-[state=on]:ring-[3px] data-[state=on]:ring-ring/50"
                  style={{ background: c }}
                />
              ))}
            </ToggleGroup>
          </Field>
        </FieldGroup>
      )}

      {source === "upload" && (
        <div className="flex w-full flex-col items-start gap-2">
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="elevated"
              onClick={() => fileInputRef.current?.click()}
            >
              {text("Choose file…", "选择文件…")}
            </Button>
            {value?.kind === "upload" && value.file && (
              <Button
                type="button"
                variant="elevated"
                onClick={() => onChange({ kind: "upload", file: undefined })}
              >
                {text("Clear", "清除")}
              </Button>
            )}
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept={UPLOAD_ACCEPT}
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void onFilePicked(f);
              e.target.value = "";
            }}
          />
          <FieldDescription>
            PNG · JPG · SVG · GIF · WebP · APNG · max{" "}
            {formatBytes(uploadMaxBytes)}. Animated GIF / WebP play in
            place.
          </FieldDescription>
          {uploadError && <FieldError>{uploadError}</FieldError>}
        </div>
      )}
    </div>
  );
}
