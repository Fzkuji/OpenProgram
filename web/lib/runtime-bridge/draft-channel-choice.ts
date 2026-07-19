import {
  readSessionDraftState,
  updateSessionDraftState,
} from "@/lib/session-draft-persistence";

export interface PendingChannelChoice {
  channel: string | null;
  account_id: string | null;
}

export interface DraftChannelChoiceHost {
  _pendingChannelChoice?: PendingChannelChoice | null;
  __pendingChannelChoices?: Record<string, PendingChannelChoice>;
}

function persistChoices(choices: Record<string, PendingChannelChoice>): void {
  updateSessionDraftState((state) => ({
    ...state,
    draftChannelChoices: choices,
  }));
}

if (typeof window !== "undefined") {
  const host = window as unknown as DraftChannelChoiceHost;
  if (!host.__pendingChannelChoices) {
    host.__pendingChannelChoices = readSessionDraftState().draftChannelChoices;
  }
}

export function setDraftChannelChoice(
  host: DraftChannelChoiceHost,
  chatKey: string | null | undefined,
  choice: PendingChannelChoice,
): void {
  host._pendingChannelChoice = choice;
  if (!chatKey) return;
  const choices = { ...host.__pendingChannelChoices };
  if (choice.channel) choices[chatKey] = choice;
  else delete choices[chatKey];
  host.__pendingChannelChoices = choices;
  persistChoices(choices);
}

export function switchDraftChannelChoice(
  host: DraftChannelChoiceHost,
  outgoingKey: string | null | undefined,
  incomingKey: string | null | undefined,
): void {
  if (outgoingKey) {
    const choices = { ...host.__pendingChannelChoices };
    const outgoing = host._pendingChannelChoice;
    if (outgoing?.channel) choices[outgoingKey] = outgoing;
    else delete choices[outgoingKey];
    host.__pendingChannelChoices = choices;
    persistChoices(choices);
  }
  host._pendingChannelChoice = incomingKey
    ? (host.__pendingChannelChoices?.[incomingKey] ?? null)
    : null;
}

export function draftChannelChoiceFor(
  host: DraftChannelChoiceHost,
  chatKey: string | null | undefined,
): PendingChannelChoice | null {
  return chatKey ? (host.__pendingChannelChoices?.[chatKey] ?? null) : null;
}

export function dropDraftChannelChoice(
  host: DraftChannelChoiceHost,
  chatKey: string,
  clearLegacyFallback = false,
): void {
  const keyedChoice = host.__pendingChannelChoices?.[chatKey] ?? null;
  if (keyedChoice) {
    const choices = { ...host.__pendingChannelChoices };
    delete choices[chatKey];
    host.__pendingChannelChoices = choices;
    persistChoices(choices);
  }
  const globalChoiceBelongsToKey =
    keyedChoice !== null && host._pendingChannelChoice === keyedChoice;
  if (
    globalChoiceBelongsToKey
    || (clearLegacyFallback && keyedChoice === null)
  ) {
    host._pendingChannelChoice = null;
  }
}
