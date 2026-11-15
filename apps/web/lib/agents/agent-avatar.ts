/**
 * How an Agent is pictured.
 *
 * An Agent that was given an avatar shows it. One that was not shows the
 * character derived from its id, so every Agent has a picture and the same
 * Agent always has the same one.
 */
import type { AvatarConfig } from "@/components/avatar/types";
import { isBotShape } from "@/components/avatar/bot/catalog";
import { botLookFor } from "@/components/avatar/bot/identity";

/** An uploaded picture is stored with the Agent and travels in every
 *  listing, so it is kept far below the profile picture's limit. The
 *  server refuses a data URL above 350,000 characters. */
export const AGENT_AVATAR_UPLOAD_MAX_BYTES = 256 * 1024;

export function agentAvatarConfig(agent: { id: string; avatar?: AvatarConfig | null }): AvatarConfig {
  const saved = agent.avatar;
  if (saved?.kind === "upload" && !saved.file) return { kind: "bot", seed: agent.id };
  if (saved?.kind) return saved.kind === "bot" || saved.kind === "dicebear" ? { seed: agent.id, ...saved } : saved;
  return { kind: "bot", seed: agent.id };
}

/** The character shape that stands for an Agent where a flat glyph is drawn
 *  (the graph, the Activity panel): its saved character's, else the one its
 *  id gives, also for an Agent pictured by an upload or a generated image. */
export function agentCharacterShape(agent: { id: string; avatar?: AvatarConfig | null }): string {
  const saved = agent.avatar;
  return saved?.kind === "bot" && isBotShape(saved.shape) ? saved.shape : botLookFor(agent.id).shape;
}
