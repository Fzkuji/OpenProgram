import type { AvatarStyle } from "./types";

/** Lightweight UI registry. Keep it separate from the DiceBear runtime
 * imports so selection logic and tests do not load every renderer. */
export const AVATAR_STYLES = [
  { id: "shapes", label: "Shapes", hint: "Abstract geometric (default)" },
  { id: "avataaars", label: "Avataaars", hint: "Sketch-style portrait characters" },
  { id: "adventurer", label: "Adventurer", hint: "Illustrated adventurer faces" },
  { id: "micah", label: "Micah", hint: "Flat illustrated portraits" },
  { id: "openPeeps", label: "Open Peeps", hint: "Hand-drawn people" },
  { id: "personas", label: "Personas", hint: "Clean vector personas" },
  { id: "bigSmile", label: "Big Smile", hint: "Cheerful cartoon faces" },
  { id: "funEmoji", label: "Fun Emoji", hint: "Simple emoji-like faces" },
  { id: "bottts", label: "Bottts", hint: "Robot avatars" },
  { id: "thumbs", label: "Thumbs", hint: "Rounded thumb characters" },
  { id: "pixelArt", label: "Pixel Art", hint: "8-bit retro characters" },
  { id: "identicon", label: "Identicon", hint: "GitHub-style geometric hash" },
  { id: "rings", label: "Rings", hint: "Concentric colour rings" },
  { id: "initials", label: "Initials", hint: "Letter on coloured chip" },
] as const satisfies readonly {
  id: AvatarStyle;
  label: string;
  hint: string;
}[];
