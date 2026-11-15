"use client";

/** The picture of one Agent, wherever the interface names it. */
import { Avatar, type AvatarConfig, type BotFitRule, type BotMood } from "@/components/avatar";
import { agentAvatarConfig, agentCharacterShape } from "@/lib/agents/agent-avatar";
import { useAgents } from "@/lib/agents/agents-query";
import { conversationAgentId } from "@/lib/agents/settle-agent-invocation";
import { useSessionStore } from "@/lib/session-store";

export interface AgentAvatarProps {
  /** The Agent, or just its id where that is all the caller has. */
  agent: { id: string; name?: string; avatar?: AvatarConfig | null };
  size: number;
  /** Animate a character avatar. On unless the caller turns it off: an
   *  Agent's character is alive wherever it stands for the Agent. */
  live?: boolean;
  mood?: BotMood;
  /** "mark" sizes a character by the brand-mark rule, for a control where
   *  it sits beside logos (the Agent chip and its menu). */
  fit?: BotFitRule;
  className?: string;
}

/** Pictures that are not characters keep the rounded-square frame the
 *  Agent surfaces use; a character is its own silhouette. */
const radiusFor = (size: number) => (size >= 36 ? 10 : size >= 28 ? 8 : 5);

export function AgentAvatar({ agent, size, live = true, mood, fit, className }: AgentAvatarProps) {
  return (
    <Avatar
      size={size}
      name={agent.name || agent.id}
      config={agentAvatarConfig(agent)}
      radius={radiusFor(size)}
      live={live}
      mood={mood}
      decorative
      fit={fit}
      className={className}
    />
  );
}

/** For places that hold only an Agent id (a message, a binding row): the
 *  saved avatar once the list has loaded, the derived character until then. */
export function AgentAvatarById({ agentId, ...rest }: Omit<AgentAvatarProps, "agent"> & { agentId: string }) {
  const { data: agents } = useAgents();
  const agent = agents?.find((entry) => entry.id === agentId) ?? { id: agentId };
  return <AgentAvatar agent={agent} {...rest} />;
}

/** The character shape of the Agent a conversation runs with (the default
 *  Agent when it names none), for the flat glyphs of the graph and the
 *  Activity panel. */
export function useConversationAgentShape(sessionId: string | null): string {
  const own = useSessionStore((state) => conversationAgentId(state, sessionId, null));
  const { data: agents } = useAgents();
  const id = own ?? agents?.find((agent) => agent.default)?.id ?? "main";
  return agentCharacterShape(agents?.find((agent) => agent.id === id) ?? { id });
}
