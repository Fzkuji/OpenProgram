"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { applicationRequest, type ApplicationInstance, type ApplicationRun } from "@/lib/net/applications";
import { useSessionStore } from "@/lib/session-store";
import { requestSessionLoad } from "@/lib/runtime-bridge/session/session-load";
import { HISTORY_PROTOCOL } from "@/lib/chat/history/session-history";
import { setApplicationNote } from "@/lib/chat/send/application-notes";
import { enqueueMessage, useSendQueue } from "@/lib/chat/send/send-queue";
import { openSession } from "@/lib/tabs/navigation/open-session";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { pushPath } from "@/lib/nav/shallow-nav";
import { useChanged } from "@/lib/net/ws/changes";
import { useAudioRecorder } from "@/lib/applications/audio-recorder";
import {
  ApplicationComposer, type ComposerPlace, setOwnConversation, useConversationFeed,
} from "@/components/applications/application-conversation";

/**
 * The host's look as an application frame applies it to its own <html>:
 * the root's data-* attributes and lang (theme, mode, style, locale) and the
 * custom properties set on it (the chosen font).
 */
function appearance(): { attributes: Record<string, string>; properties: Record<string, string> } {
  const root = document.documentElement;
  const attributes: Record<string, string> = {};
  for (const name of root.getAttributeNames()) {
    if (name.startsWith("data-") || name === "lang") attributes[name] = root.getAttribute(name) ?? "";
  }
  const properties: Record<string, string> = {};
  for (let index = 0; index < root.style.length; index++) {
    const name = root.style.item(index);
    if (name.startsWith("--")) properties[name] = root.style.getPropertyValue(name);
  }
  return { attributes, properties };
}

/** Instances already read, so a tab shown again draws its frame at once. */
const instancesRead = new Map<string, ApplicationInstance>();

function readPlace(value: unknown): ComposerPlace | null {
  if (!value || typeof value !== "object") return null;
  const { left, right, bottom } = value as Record<string, unknown>;
  if (![left, right, bottom].every((n) => typeof n === "number" && Number.isFinite(n) && n >= 0 && n < 100_000)) return null;
  return { left: left as number, right: right as number, bottom: bottom as number };
}

export function ApplicationTabPane({ instanceId }: { instanceId: string }) {
  const [instance, setInstance] = useState<ApplicationInstance | null>(() => instancesRead.get(instanceId) ?? null);
  const channel = useRef<MessageChannel | null>(null);
  // The conversation the window shows (capability `chat`) and where its composer goes.
  const [conversation, setConversation] = useState<string | null>(null);
  const [place, setPlace] = useState<ComposerPlace | null>(null);
  const conversationRef = useRef<string | null>(null);
  const base = `/api/application-instances/${encodeURIComponent(instanceId)}`;
  useEffect(() => {
    let disposed = false;
    let failures = 0;
    let retry: ReturnType<typeof setTimeout> | undefined;
    setInstance(instancesRead.get(instanceId) ?? null);
    // A read that fails keeps what is drawn and is asked again, quietly.
    const read = () => {
      applicationRequest<ApplicationInstance>(base).then(value => {
        instancesRead.set(instanceId, value);
        if (!disposed) setInstance(value);
      }).catch((reason: unknown) => {
        if (disposed) return;
        console.warn("application instance read failed; reading again", { instanceId, reason });
        retry = setTimeout(read, Math.min(30_000, 1000 * 2 ** failures++));
      });
    };
    read();
    return () => { disposed = true; clearTimeout(retry); channel.current?.port1.close(); channel.current?.port2.close(); };
  }, [base, instanceId]);

  // A closed window stops telling its conversation what messages are about.
  useEffect(() => () => { if (conversationRef.current) setApplicationNote(conversationRef.current, null); }, []);

  // A window that declares chat keeps its conversations itself, so the right
  // sidebar stays away from its first frame, not only once a conversation opens.
  const holds = Boolean(instance?.application.capabilities?.includes("chat"));
  useEffect(() => { setOwnConversation(instanceId, holds); }, [instanceId, holds]);
  useEffect(() => () => setOwnConversation(instanceId, false), [instanceId]);

  const post = useCallback((event: string, value: unknown) => {
    channel.current?.port1.postMessage({ event, value });
  }, []);
  useConversationFeed(conversation, post);

  // What the frame hears without asking: its state document was saved, the
  // host's recording changed, the App's look changed.
  useChanged("application-state", () => post("state", null), [instanceId]);
  useChanged("application-audio", () => post("audio", null), [instanceId]);
  const microphone = useAudioRecorder(state => state.microphones[instanceId]);
  useEffect(() => { post("audio", null); }, [microphone, post]);
  // The App's look is followed only while a frame is connected (see connect).
  const look = useRef<MutationObserver | null>(null);
  useEffect(() => () => look.current?.disconnect(), []);

  function connect(frame: HTMLIFrameElement) {
    if (!instance || !frame.contentWindow) return;
    channel.current?.port1.close();
    channel.current?.port2.close();
    const connection = new MessageChannel();
    channel.current = connection;
    const definition = instance.application;
    const chatAllowed = () => {
      if (!definition.capabilities?.includes("chat")) throw new Error("application does not declare chat");
    };
    const audioAllowed = () => {
      if (!definition.capabilities?.includes("audio.record")) throw new Error("application does not declare audio.record");
    };
    connection.port1.onmessage = async ({ data }) => {
      if (!data || typeof data.id !== "string" || data.id.length > 128) return;
      const { id, method } = data;
      const params = data.params ?? {};
      try {
        let result: unknown;
        if (method === "state.load") result = await applicationRequest(`${base}/state`);
        else if (method === "state.save") result = await applicationRequest(`${base}/state`, "PUT", { value: params.value, version: params.version });
        else if (method === "runs.list") result = (await applicationRequest<ApplicationInstance>(base)).runs;
        else if (method === "operation.run") {
          if (typeof params.operation !== "string") throw new Error("An operation name is required");
          result = await applicationRequest(`${base}/operations/${encodeURIComponent(params.operation)}`, "POST", {
            digest: definition.digest, input: params.input, request_key: params.request_key,
          });
        } else if (["run.status", "run.cancel", "run.answer"].includes(method)) {
          if (typeof params.id !== "string" || !/^app_[a-f0-9]{32}$/.test(params.id)) throw new Error("Invalid run identifier");
          const runUrl = `/api/application-runs/${params.id}`;
          const run = await applicationRequest<ApplicationRun>(runUrl);
          if (run.instance_id !== instanceId) throw new Error("This run belongs to another application instance");
          if (method === "run.status") {
            const after = Number.isSafeInteger(params.after) && params.after >= 0 ? params.after : 0;
            result = await applicationRequest(`${runUrl}?after=${after}`);
          } else result = await applicationRequest(`${runUrl}/${method === "run.cancel" ? "cancel" : "answer"}`, "POST", {
            request_id: params.request_id, answer: params.answer,
          });
        } else if (method === "audio.start") {
          audioAllowed();
          if (typeof params.key !== "string") throw new Error("A recording key is required");
          result = await useAudioRecorder.getState().start(instanceId, {
            key: params.key,
            tracks: Array.isArray(params.tracks) ? params.tracks.filter((track: unknown) => track === "mic" || track === "system") : undefined,
            max_seconds: Number.isFinite(params.max_seconds) ? params.max_seconds : undefined,
          });
        } else if (method === "audio.stop") {
          audioAllowed();
          result = await useAudioRecorder.getState().stop(instanceId);
        } else if (method === "audio.status") {
          audioAllowed();
          result = await useAudioRecorder.getState().status(instanceId);
        } else if (method === "conversation.show") {
          // A conversation the application's backend opened (capability `conversation`).
          if (typeof params.session_id !== "string" || !params.session_id) throw new Error("A conversation is required");
          const sid = params.session_id;
          await applicationRequest(`${base}/conversations/${encodeURIComponent(sid)}/show`, "POST", {});
          useCenterTabs.getState().openSessionTab(sid, useSessionStore.getState().conversations[sid]?.title ?? "");
          pushPath(`/s/${sid}`);
          result = true;
        } else if (method === "chat.open") {
          chatAllowed();
          const opened = await applicationRequest<{ session_id: string; project_id: string; folder: string }>(
            `${base}/conversations`, "POST", { key: params.key, folder: params.folder });
          const sid = opened.session_id;
          const store = useSessionStore.getState();
          const exists = Boolean(store.conversations[sid]);
          // A conversation's first message creates it inside the folder.
          if (!exists) store.setPendingProject(sid, opened.project_id);
          else requestSessionLoad({ action: "load_session", session_id: sid, history_version: HISTORY_PROTOCOL });
          if (conversationRef.current && conversationRef.current !== sid) setApplicationNote(conversationRef.current, null);
          conversationRef.current = sid;
          setConversation(sid);
          result = { session_id: sid, folder: opened.folder, exists };
        } else if (method === "chat.note") {
          chatAllowed();
          if (!conversationRef.current) throw new Error("No conversation is open");
          setApplicationNote(conversationRef.current, typeof params.text === "string" ? params.text.slice(0, 16_000) : null);
          result = true;
        } else if (method === "chat.send") {
          // A button of the window that says something for the owner, such as
          // "draft this task": sent as their message, after a running reply.
          chatAllowed();
          const sid = conversationRef.current;
          if (!sid) throw new Error("No conversation is open");
          const text = typeof params.text === "string" ? params.text.trim().slice(0, 16_000) : "";
          if (!text) throw new Error("A message is required");
          if (!enqueueMessage(sid, { text, thinking: "", webSearchEnabled: false, background: true })) throw new Error("The message was not queued");
          useSendQueue.getState().drain(sid);
          result = true;
        } else if (method === "chat.place") {
          chatAllowed();
          setPlace(readPlace(params.rect));
          result = true;
        } else if (method === "chat.reveal") {
          chatAllowed();
          const sid = conversationRef.current;
          if (!sid || !useSessionStore.getState().conversations[sid]) result = false;
          else { openSession(sid); result = true; }
        } else if (method === "chat.close") {
          chatAllowed();
          if (conversationRef.current) setApplicationNote(conversationRef.current, null);
          conversationRef.current = null;
          setConversation(null);
          result = true;
        } else throw new Error("Unsupported application request");
        connection.port1.postMessage({ id, result });
      } catch (reason) {
        connection.port1.postMessage({ id, error: reason instanceof Error ? reason.message : String(reason) });
      }
    };
    frame.contentWindow.postMessage({ type: "openprogram.application.connect" }, "*", [connection.port2]);
    connection.port1.postMessage({ event: "appearance", value: appearance() });
    look.current?.disconnect();
    look.current = new MutationObserver(() => post("appearance", appearance()));
    look.current.observe(document.documentElement, { attributes: true });
  }

  if (!instance) return null;
  const definition = instance.application;
  return (
    <div className="relative h-full w-full">
      <iframe
        title={definition.display_title || definition.title}
        src={instance.ui_url}
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        className="h-full w-full border-0"
        onLoad={event => {
          if (conversationRef.current) setApplicationNote(conversationRef.current, null);
          conversationRef.current = null; setConversation(null); setPlace(null);
          connect(event.currentTarget);
        }}
      />
      {conversation && place ? (
        <ApplicationComposer key={conversation} sessionId={conversation} place={place}
          onHeight={(height) => post("composer", { height })} />
      ) : null}
    </div>
  );
}
